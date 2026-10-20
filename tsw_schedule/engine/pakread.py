"""List or extract files in an unencrypted UE 4.26 (pak v11) .pak using its full directory index.

Usage:
  python paklist.py <file.pak> [substring-filter ...]               list matching files
  python paklist.py <file.pak> --extract <out-dir> substring ...    extract matching files
"""
import os
import struct
import sys
import zlib

PAK_MAGIC = 0x5A6F12E1
COMPRESSION_NAME_COUNT = 5
FOOTER_SIZE = 16 + 1 + 4 + 4 + 8 + 8 + 20 + COMPRESSION_NAME_COUNT * 32


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, n):
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u32(self):
        return struct.unpack("<I", self.read(4))[0]

    def i32(self):
        return struct.unpack("<i", self.read(4))[0]

    def i64(self):
        return struct.unpack("<q", self.read(8))[0]

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ""
        if n > 0:
            return self.read(n)[:-1].decode("latin-1")
        return self.read(-n * 2)[:-2].decode("utf-16-le")


class Pak:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            f.seek(-FOOTER_SIZE, 2)
            footer = f.read(FOOTER_SIZE)
            encrypted, magic, self.version, index_offset, index_size = struct.unpack_from("<BIiqq", footer, 16)
            if magic != PAK_MAGIC:
                raise ValueError("pak magic not found - footer layout differs from v11")
            if encrypted:
                raise ValueError("index is encrypted")
            names = footer[-COMPRESSION_NAME_COUNT * 32:]
            self.compression = [""] + [names[i * 32:(i + 1) * 32].rstrip(b"\0").decode() for i in range(COMPRESSION_NAME_COUNT)]

            f.seek(index_offset)
            r = Reader(f.read(index_size))
            self.mount_point = r.fstring()
            self.num_entries = r.i32()
            r.i64()  # path hash seed
            if r.i32():  # has path hash index
                r.i64(); r.i64(); r.read(20)
            if not r.i32():
                raise ValueError("pak has no full directory index (names pruned)")
            fdi_offset, fdi_size = r.i64(), r.i64()
            r.read(20)
            self.encoded_entries = r.read(r.i32())

            f.seek(fdi_offset)
            r = Reader(f.read(fdi_size))
            self.files = {}
            for _ in range(r.i32()):
                directory = r.fstring()
                for _ in range(r.i32()):
                    name = r.fstring()
                    self.files[directory + name] = r.i32()

    def entry_offset(self, encoded_offset):
        """Decode just the file offset from an encoded (bit-packed) pak entry."""
        r = Reader(self.encoded_entries)
        r.pos = encoded_offset
        bits = r.u32()
        if bits & 0x3F == 0x3F:
            r.u32()  # explicit compression block size
        return r.u32() if bits & (1 << 31) else r.i64()

    def extract(self, name):
        encoded = self.files[name]
        if encoded < 0:
            raise ValueError(f"{name}: entry stored outside the encoded table, not supported")
        offset = self.entry_offset(encoded)
        with open(self.path, "rb") as f:
            # Each file's data is preceded by a legacy-serialized FPakEntry header.
            f.seek(offset)
            head = Reader(f.read(8 + 8 + 8 + 4 + 20 + 4))
            head.i64()  # offset (0 in the inline copy)
            size, uncompressed_size = head.i64(), head.i64()
            method = head.u32()
            head.read(20)  # hash
            header_size = head.pos
            blocks = []
            if method:
                count = head.i32()
                block_data = Reader(f.read(count * 16))
                blocks = [(block_data.i64(), block_data.i64()) for _ in range(count)]
                header_size += 4 + count * 16
            else:
                f.seek(offset + header_size)
            flags = f.read(1)[0]
            f.read(4)  # compression block size
            header_size += 5
            if flags & 1:
                raise ValueError(f"{name}: file data is encrypted")
            if not method:
                f.seek(offset + header_size)
                return f.read(uncompressed_size)
            if self.compression[method] != "Zlib":
                raise ValueError(f"{name}: unsupported compression {self.compression[method]}")
            out = bytearray()
            for start, end in blocks:  # block offsets are relative to the entry offset
                f.seek(offset + start)
                out += zlib.decompress(f.read(end - start))
            return bytes(out[:uncompressed_size])


def main():
    pak = Pak(sys.argv[1])
    args = sys.argv[2:]
    if args[:1] == ["--extract"]:
        out_dir, filters = args[1], [s.lower() for s in args[2:]]
        for name in sorted(pak.files):
            if any(s in name.lower() for s in filters):
                dest = os.path.join(out_dir, name)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as out:
                    out.write(pak.extract(name))
                print("extracted", name)
        return
    filters = [s.lower() for s in args]
    print(f"# version={pak.version} mount={pak.mount_point} entries={pak.num_entries}")
    for name in sorted(pak.files):
        if not filters or any(s in name.lower() for s in filters):
            print(pak.mount_point + name)


if __name__ == "__main__":
    main()
