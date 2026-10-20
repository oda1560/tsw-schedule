"""Read and write cooked AssetRegistry.bin files as TSW7 ships them (FAssetRegistryVersion 7, with a name table).

Layout: version GUID + int32 version, int64 offset of the name table, then
  int32 asset count, per asset: ObjectPath, PackagePath, AssetClass, PackageName, AssetName (FNames),
      tags (int32 count, then FName key + FString value), ChunkIDs (int32 count + int32s), PackageFlags (uint32)
  int32 dependency-node count, per node: identifier, then dependency lists
  int32 package count, per package: FName name + package data
and finally the name table (int32 count, per name: FString + two 16-bit hashes).

Usage: python assetregistry.py <AssetRegistry.bin> [name filter]   - print the assets
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uasset_edit import fstring_bytes, name_hashes  # noqa: E402

REGISTRY_GUID = bytes.fromhex("e79e7f713a49b0e93291b3880781381b")


class _R:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def take(self, fmt):
        v = struct.unpack_from(fmt, self.d, self.p)
        self.p += struct.calcsize(fmt)
        return v if len(v) > 1 else v[0]

    def fstring(self):
        n = self.take("<i")
        if n >= 0:
            s = self.d[self.p:self.p + n][:-1].decode("latin-1") if n else ""
            self.p += n
        else:
            s = self.d[self.p:self.p - 2 * n][:-2].decode("utf-16-le")
            self.p -= 2 * n
        return s


class Registry:
    def __init__(self):
        self.version = 7
        self.assets = []         # dicts: object_path, package_path, asset_class, package_name, asset_name, tags, chunks, flags
        self.depends_raw = b""   # dependency section, kept as bytes (int32 count + nodes)
        self.packages_raw = b""  # package-data section, kept as bytes

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            data = f.read()
        reg = cls()
        if data[:16] != REGISTRY_GUID:
            raise ValueError("not an asset registry")
        reg.version = struct.unpack_from("<i", data, 16)[0]
        names_at = struct.unpack_from("<q", data, 20)[0]
        nr = _R(data, names_at)
        names = []
        for _ in range(nr.take("<i")):
            names.append(nr.fstring())
            nr.take("<I")
        reg.names = names

        r = _R(data, 28)

        def fname():
            i, n = r.take("<ii")
            return names[i] if n == 0 else f"{names[i]}_{n - 1}"

        for _ in range(r.take("<i")):
            asset = {k: fname() for k in ("object_path", "package_path", "asset_class", "package_name", "asset_name")}
            asset["tags"] = [(fname(), r.fstring()) for _ in range(r.take("<i"))]
            asset["chunks"] = [r.take("<i") for _ in range(r.take("<i"))]
            asset["flags"] = r.take("<I")
            reg.assets.append(asset)
        reg.body_after_assets = data[r.p:names_at]
        reg.names_at = names_at
        return reg

    def save(self, path):
        names, index = [], {}

        def fname(text):
            # Like FName("Foo_3"): a numeric suffix is stored as the number + 1 next to the base name "Foo".
            base, number = text, 0
            head, sep, tail = text.rpartition("_")
            if sep and head and tail.isdigit() and (tail == "0" or not tail.startswith("0")):
                base, number = head, int(tail) + 1
            if base not in index:
                index[base] = len(names)
                names.append(base)
            return struct.pack("<ii", index[base], number)

        body = struct.pack("<i", len(self.assets))
        for a in self.assets:
            body += b"".join(fname(a[k]) for k in
                             ("object_path", "package_path", "asset_class", "package_name", "asset_name"))
            body += struct.pack("<i", len(a["tags"]))
            body += b"".join(fname(k) + fstring_bytes(v) for k, v in a["tags"])
            body += struct.pack("<i", len(a["chunks"])) + b"".join(struct.pack("<i", c) for c in a["chunks"])
            body += struct.pack("<I", a["flags"])
        body += self.depends_raw + self.packages_raw
        header = REGISTRY_GUID + struct.pack("<i", self.version)
        names_at = len(header) + 8 + len(body)
        table = struct.pack("<i", len(names))
        table += b"".join(fstring_bytes(n) + struct.pack("<HH", *name_hashes(n)) for n in names)
        with open(path, "wb") as f:
            f.write(header + struct.pack("<q", names_at) + body + table)


def main():
    reg = Registry.load(sys.argv[1])
    flt = sys.argv[2] if len(sys.argv) > 2 else ""
    print(f"version {reg.version}, {len(reg.assets)} assets, {len(reg.names)} names, "
          f"{len(reg.body_after_assets)} bytes of dependency/package data")
    for a in reg.assets:
        if flt in a["object_path"] or flt == a["asset_class"]:
            print(f"{a['asset_class']}  {a['object_path']}  chunks={a['chunks']} flags=0x{a['flags']:x}")
            for k, v in a["tags"]:
                print(f"    {k} = {v[:150]}")


if __name__ == "__main__":
    main()
