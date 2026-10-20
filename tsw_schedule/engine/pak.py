"""Build an uncompressed UE 4.26 (pak v11) .pak from a folder, laid out like the game's own paks.

Usage: python pakbuild.py <folder> <out.pak>
Files are stored at their path relative to <folder> under the mount point "../../../" (the WindowsNoEditor folder),
so <folder>/TS2Prototype/Plugins/DLC/... overrides the game's file at the same path when this pak is mounted with a
higher priority than the original (a name ending in _P.pak).
Like the game's paks it carries a path-hash index (FNV-64 of the lower-case UTF-16 path, seeded) followed by a pruned
directory index, plus the full directory index.
"""
import hashlib
import os
import struct
import sys
import zlib

PAK_MAGIC = 0x5A6F12E1
PAK_VERSION = 11
MOUNT_POINT = "../../../"
# Files the game's pruned directory index keeps (UE's defaults); every directory is kept, empty or not.
PRUNED_KEEP = (".uplugin", ".upluginmanifest", ".ini", ".locmeta", ".locres")
U64 = 0xFFFFFFFFFFFFFFFF


def fstring(text):
    data = text.encode("ascii") + b"\0"
    return struct.pack("<i", len(data)) + data


def path_hash(path, seed):
    h = (0xCBF29CE484222325 + seed) & U64
    for byte in path.lower().encode("utf-16-le"):
        h = ((h ^ byte) * 0x100000001B3) & U64
    return h


def directory_index(directories, keep=lambda name: True):
    out = struct.pack("<i", len(directories))
    for directory, entries in directories.items():
        kept = {name: loc for name, loc in entries.items() if keep(name)}
        out += fstring(directory) + struct.pack("<i", len(kept))
        for name, encoded_offset in kept.items():
            out += fstring(name) + struct.pack("<i", encoded_offset)
    return out


def inline_entry_header(size, sha1):
    """FPakEntry as serialized in front of each file's data (uncompressed, not encrypted)."""
    return struct.pack("<qqqI", 0, size, size, 0) + sha1 + struct.pack("<BI", 0, 0)


def encoded_entry(offset, size):
    """The bit-packed FPakEntry the primary index stores for an uncompressed file."""
    bits = 0
    if offset < 1 << 32:
        bits |= 1 << 31
    if size < 1 << 32:
        bits |= 1 << 30 | 1 << 29
    out = struct.pack("<I", bits)
    out += struct.pack("<I", offset) if offset < 1 << 32 else struct.pack("<q", offset)
    out += struct.pack("<I", size) if size < 1 << 32 else struct.pack("<q", size)
    return out


def build(folder, out_path):
    files = []
    for root, _, names in os.walk(folder):
        for name in names:
            full = os.path.join(root, name)
            files.append((os.path.relpath(full, folder).replace(os.sep, "/"), full))
    files.sort()

    encoded = bytearray()
    directories = {"/": {}}
    with open(out_path, "wb") as out:
        for rel, full in files:
            with open(full, "rb") as f:
                data = f.read()
            offset = out.tell()
            out.write(inline_entry_header(len(data), hashlib.sha1(data).digest()))
            out.write(data)

            directory, name = rel.rsplit("/", 1)
            directories.setdefault(directory + "/", {})[name] = len(encoded)
            encoded += encoded_entry(offset, len(data))
            # Every parent directory is listed too, as the game's paks do.
            parts = directory.split("/")
            for i in range(1, len(parts)):
                directories.setdefault("/".join(parts[:i]) + "/", {})
            print(f"  {rel} ({len(data)} bytes)")

        seed = zlib.crc32(os.path.basename(out_path).lower().encode())
        path_hashes = {}
        for directory, entries in directories.items():
            for name, encoded_offset in entries.items():
                path_hashes[path_hash(directory.lstrip("/") + name, seed)] = encoded_offset
        phi = struct.pack("<i", len(path_hashes))
        phi += b"".join(struct.pack("<Qi", h, loc) for h, loc in path_hashes.items())
        phi += directory_index(directories, keep=lambda name: name.lower().endswith(PRUNED_KEEP))
        fdi = directory_index(directories)

        # Both secondary indexes sit after the primary index, so their offsets are known once its size is.
        def primary(phi_offset):
            fdi_offset = phi_offset + len(phi)
            index = fstring(MOUNT_POINT) + struct.pack("<iQ", len(files), seed)
            index += struct.pack("<iqq", 1, phi_offset, len(phi)) + hashlib.sha1(phi).digest()
            index += struct.pack("<iqq", 1, fdi_offset, len(fdi)) + hashlib.sha1(fdi).digest()
            index += struct.pack("<i", len(encoded)) + bytes(encoded)
            index += struct.pack("<i", 0)  # no entries that need the unencoded form
            return index

        index_offset = out.tell()
        index = primary(0)
        index = primary(index_offset + len(index))
        out.write(index)
        out.write(phi)
        out.write(fdi)

        footer = bytes(16) + struct.pack("<BIiqq", 0, PAK_MAGIC, PAK_VERSION, index_offset, len(index))
        footer += hashlib.sha1(index).digest()
        footer += b"".join(name.ljust(32, b"\0") for name in (b"Zlib", b"", b"", b"", b""))
        out.write(footer)
    print(f"wrote {out_path}: {len(files)} files, {os.path.getsize(out_path)} bytes")


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
