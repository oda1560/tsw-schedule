"""Edit cooked UE 4.26 packages: rename name-map entries and resize exports, keeping every header offset consistent.

A cooked package is a .uasset (summary, name map, imports, exports table, ...) plus a .uexp (export data, then the
package tag). Export SerialOffsets count from the start of the .uasset, so growing the name map shifts them too.

    pkg = EditablePackage.load("WCMLTT.uasset")
    pkg.rename_names({"/Old/Path": "/New/Path"})
    pkg.replace_export_data(1, new_bytes)
    pkg.save("out/WCMLTT.uasset")
"""
import struct
import zlib

PACKAGE_TAG = 0x9E2A83C1
PKG_FILTER_EDITOR_ONLY = 0x80000000
UE4_LATEST = 522
EXPORT_SIZE = 104
EXPORT_SERIAL_SIZE_AT = 28
EXPORT_SERIAL_OFFSET_AT = 36


def _crc_table_msb():
    table = []
    for i in range(256):
        crc = i << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else (crc << 1)
        table.append(crc & 0xFFFFFFFF)
    return table


CRC_TABLE_DEPRECATED = _crc_table_msb()


def strihash_deprecated(text):
    """FCrc::StrihashDeprecated: case-insensitive hash of an FName (one byte per char for ANSI names)."""
    wide = any(ord(c) > 0x7F for c in text)
    h = 0
    for c in text.upper():
        code = ord(c)
        for byte in ((code & 0xFF, code >> 8 & 0xFF) if wide else (code & 0xFF,)):
            h = ((h >> 8) & 0x00FFFFFF) ^ CRC_TABLE_DEPRECATED[(h ^ byte) & 0xFF]
    return h


def str_crc32(text):
    """FCrc::StrCrc32: CRC-32 over every character widened to 4 bytes."""
    return zlib.crc32(b"".join(ord(c).to_bytes(4, "little") for c in text))


def name_hashes(text):
    return strihash_deprecated(text) & 0xFFFF, str_crc32(text) & 0xFFFF


def fstring_bytes(text):
    if all(ord(c) < 0x80 for c in text):
        data = text.encode("ascii") + b"\0"
        return struct.pack("<i", len(data)) + data
    data = text.encode("utf-16-le") + b"\0\0"
    return struct.pack("<i", -(len(data) // 2)) + data


class _Reader:
    def __init__(self, data, pos=0):
        self.data, self.pos = data, pos

    def take(self, fmt):
        value = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += struct.calcsize(fmt)
        return value if len(value) > 1 else value[0]

    def fstring(self):
        n = self.take("<i")
        if n >= 0:
            text = self.data[self.pos:self.pos + n][:-1].decode("latin-1") if n else ""
            self.pos += n
        else:
            text = self.data[self.pos:self.pos - 2 * n][:-2].decode("utf-16-le")
            self.pos -= 2 * n
        return text


class EditablePackage:
    def __init__(self, header, body):
        self.header = bytearray(header)
        self.body = bytearray(body)
        self._parse()

    @classmethod
    def load(cls, uasset_path):
        with open(uasset_path, "rb") as f:
            header = f.read()
        with open(uasset_path[:-len(".uasset")] + ".uexp", "rb") as f:
            body = f.read()
        return cls(header, body)

    def save(self, uasset_path):
        with open(uasset_path, "wb") as f:
            f.write(self.header)
        with open(uasset_path[:-len(".uasset")] + ".uexp", "wb") as f:
            f.write(self.body)

    # Summary fields that hold an absolute offset, recorded as (position, struct format).
    def _parse(self):
        r = _Reader(self.header)
        if r.take("<I") != PACKAGE_TAG:
            raise ValueError("not a uasset")
        legacy = r.take("<i")
        if legacy != -4:
            r.take("<i")
        self.ue4_version = r.take("<i") or UE4_LATEST
        r.take("<i")
        if legacy <= -2:
            for _ in range(r.take("<i")):
                r.pos += 20
        self.offset_fields = []

        def offset(fmt="<i"):
            self.offset_fields.append((r.pos, fmt))
            return r.take(fmt)

        offset()  # TotalHeaderSize
        r.fstring()  # FolderName
        self.flags = r.take("<I")
        self.name_count_pos = r.pos
        self.name_count = r.take("<i")
        self.name_offset = r.take("<i")
        if self.ue4_version >= 516 and not self.flags & PKG_FILTER_EDITOR_ONLY:
            r.fstring()
        if self.ue4_version >= 459:
            r.take("<i")
            offset()  # GatherableTextDataOffset
        self.export_count = r.take("<i")
        self.export_offset_pos = r.pos
        offset()  # ExportOffset
        r.take("<i")
        offset()  # ImportOffset
        offset()  # DependsOffset
        if self.ue4_version >= 384:
            r.take("<i")
            offset()  # SoftPackageReferencesOffset
        if self.ue4_version >= 510:
            offset()  # SearchableNamesOffset
        offset()  # ThumbnailTableOffset
        r.pos += 16  # Guid
        self.generation_name_count_pos = []
        for _ in range(r.take("<i")):  # Generations: (ExportCount, NameCount)
            self.generation_name_count_pos.append(r.pos + 4)
            r.pos += 8
        for _ in range(2):  # SavedByEngineVersion, CompatibleWithEngineVersion
            r.pos += 2 + 2 + 2 + 4
            r.fstring()
        r.take("<I")  # CompressionFlags
        if r.take("<i"):
            raise ValueError("compressed chunks in package summary are not supported")
        r.take("<I")  # PackageSource
        for _ in range(r.take("<i")):
            r.fstring()
        if legacy > -7:
            r.take("<i")
        offset()  # AssetRegistryDataOffset
        offset("<q")  # BulkDataStartOffset
        offset()  # WorldTileInfoDataOffset
        for _ in range(r.take("<i")):  # ChunkIDs
            r.take("<i")
        r.take("<i")
        offset()  # PreloadDependencyOffset
        self.summary_end = r.pos

        r = _Reader(self.header, self.name_offset)
        self.names = []
        for _ in range(self.name_count):
            self.names.append(r.fstring())
            r.pos += 4
        self.name_map_end = r.pos

    def _get(self, pos, fmt):
        return struct.unpack_from(fmt, self.header, pos)[0]

    def _shift_offsets(self, after, delta, include_header_size=True):
        """Add delta to every summary offset and export SerialOffset that points past `after`."""
        for pos, fmt in self.offset_fields:
            value = self._get(pos, fmt)
            if value > after:
                struct.pack_into(fmt, self.header, pos, value + delta)
        export_offset = self._get(self.export_offset_pos, "<i")
        for i in range(self.export_count):
            pos = export_offset + i * EXPORT_SIZE + EXPORT_SERIAL_OFFSET_AT
            value = self._get(pos, "<q")
            if value > after:
                struct.pack_into("<q", self.header, pos, value + delta)

    def _write_name_map(self, names):
        blob = b"".join(fstring_bytes(n) + struct.pack("<HH", *name_hashes(n)) for n in names)
        delta = len(blob) - (self.name_map_end - self.name_offset)
        self.header[self.name_offset:self.name_map_end] = blob
        struct.pack_into("<i", self.header, self.name_count_pos, len(names))
        for pos in self.generation_name_count_pos:
            struct.pack_into("<i", self.header, pos, len(names))
        self._shift_offsets(self.name_offset, delta)
        self._parse()

    def rename_names(self, mapping):
        """Replace name-map strings (exact matches) keeping their indices, so every reference follows."""
        changed = [n for n in self.names if n in mapping]
        if changed:
            self._write_name_map([mapping.get(n, n) for n in self.names])
        return len(changed)

    def add_names(self, texts):
        """Append names not yet in the name map (export data refers to names by index, so appending is safe)."""
        new = [t for t in dict.fromkeys(texts) if t not in self._name_index()]
        if new:
            self._write_name_map(self.names + new)
        return len(new)

    def _name_index(self):
        if getattr(self, "_index_for", None) is not self.names:
            self._index = {n: i for i, n in enumerate(self.names)}
            self._index_for = self.names
        return self._index

    def export_data(self, index):
        """Bytes of export `index` (1-based) from the .uexp."""
        pos = self._get(self.export_offset_pos, "<i") + (index - 1) * EXPORT_SIZE
        size = self._get(pos + EXPORT_SERIAL_SIZE_AT, "<q")
        start = self._get(pos + EXPORT_SERIAL_OFFSET_AT, "<q") - len(self.header)
        return bytes(self.body[start:start + size])

    def replace_export_data(self, index, data):
        pos = self._get(self.export_offset_pos, "<i") + (index - 1) * EXPORT_SIZE
        size = self._get(pos + EXPORT_SERIAL_SIZE_AT, "<q")
        serial_offset = self._get(pos + EXPORT_SERIAL_OFFSET_AT, "<q")
        start = serial_offset - len(self.header)
        delta = len(data) - size
        self.body[start:start + size] = data
        struct.pack_into("<q", self.header, pos + EXPORT_SERIAL_SIZE_AT, len(data))
        if delta:
            # Later exports and the bulk data start move with it (both count from the start of the .uasset).
            self._shift_offsets(serial_offset, delta)

    def name_index(self, text):
        return self._name_index()[text]


def self_test(uasset_path):
    pkg = EditablePackage.load(uasset_path)
    r = _Reader(pkg.header, pkg.name_offset)
    bad = 0
    for name in pkg.names:
        r.fstring()
        stored = r.take("<HH")
        if stored != name_hashes(name):
            bad += 1
    print(f"{uasset_path}: {len(pkg.names)} names, {bad} hash mismatches")
    for pos, fmt in pkg.offset_fields:
        print(f"  offset field at {pos}: {pkg._get(pos, fmt)}")
    print(f"  name map {pkg.name_offset}..{pkg.name_map_end}, header {len(pkg.header)} bytes, body {len(pkg.body)}")


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        self_test(path)
