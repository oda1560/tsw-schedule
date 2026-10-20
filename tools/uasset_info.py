"""Print the header of a cooked UE 4.26 .uasset: package flags, imports, and exports grouped by class.

Usage: python uasset_info.py <file.uasset> [--names] [--exports]
"""
import collections
import struct
import sys

PACKAGE_TAG = 0x9E2A83C1
PKG_UNVERSIONED_PROPERTIES = 0x2000
PKG_FILTER_EDITOR_ONLY = 0x80000000
UE4_LATEST = 522  # what an unversioned (0) cooked package means for 4.26
EXPORT_SIZE = 104


class Reader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def read(self, n):
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u16(self):
        return struct.unpack("<H", self.read(2))[0]

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


class Package:
    def __init__(self, data):
        r = Reader(data)
        if r.u32() != PACKAGE_TAG:
            raise ValueError("not a uasset")
        legacy = r.i32()
        if legacy != -4:
            r.i32()
        self.ue4_version = r.i32() or UE4_LATEST
        r.i32()  # licensee version
        if legacy <= -2:
            for _ in range(r.i32()):
                r.read(20)
        r.i32()  # total header size
        r.fstring()  # folder name
        self.flags = r.u32()
        name_count, name_offset = r.i32(), r.i32()
        if self.ue4_version >= 516 and not self.flags & PKG_FILTER_EDITOR_ONLY:
            r.fstring()  # localization id
        if self.ue4_version >= 459:
            r.i32(); r.i32()  # gatherable text data
        export_count, export_offset = r.i32(), r.i32()
        import_count, import_offset = r.i32(), r.i32()

        r = Reader(data, name_offset)
        self.names = []
        for _ in range(name_count):
            self.names.append(r.fstring())
            if self.ue4_version >= 504:
                r.u16(); r.u16()

        r = Reader(data, import_offset)
        self.imports = []
        for _ in range(import_count):
            class_package, class_name = self.fname(r), self.fname(r)
            outer = r.i32()
            self.imports.append((class_package, class_name, outer, self.fname(r)))

        self.exports = []
        for i in range(export_count):
            r = Reader(data, export_offset + i * EXPORT_SIZE)
            class_index, super_index, template_index, outer = r.i32(), r.i32(), r.i32(), r.i32()
            name = self.fname(r)
            r.u32()  # object flags
            serial_size, serial_offset = r.i64(), r.i64()
            self.exports.append((self.resolve(class_index), name, outer, serial_size, serial_offset))

    def fname(self, r):
        index, number = r.i32(), r.i32()
        name = self.names[index]
        return f"{name}_{number - 1}" if number else name

    def resolve(self, index):
        if index < 0:
            _, _, outer, name = self.imports[-index - 1]
            return f"{self.resolve(outer)}.{name}" if outer else name
        if index > 0:
            return self.exports[index - 1][1] if index - 1 < len(self.exports) else f"export#{index}"
        return "<none>"


def main():
    path = sys.argv[1]
    with open(path, "rb") as f:
        pkg = Package(f.read())
    print(f"# {path}")
    print(f"ue4_version={pkg.ue4_version} flags=0x{pkg.flags:08X} "
          f"unversioned_properties={bool(pkg.flags & PKG_UNVERSIONED_PROPERTIES)} "
          f"names={len(pkg.names)} imports={len(pkg.imports)} exports={len(pkg.exports)}")

    print("\n## Script classes imported")
    for class_package, class_name, outer, name in pkg.imports:
        if class_name == "Class" and outer < 0 and pkg.imports[-outer - 1][3].startswith("/Script/"):
            print(f"  {pkg.imports[-outer - 1][3]}.{name}")

    print("\n## Exports by class (count, total bytes)")
    groups = collections.defaultdict(lambda: [0, 0])
    for class_name, _, _, size, _ in pkg.exports:
        groups[class_name][0] += 1
        groups[class_name][1] += size
    for class_name, (count, size) in sorted(groups.items(), key=lambda kv: -kv[1][1]):
        print(f"  {count:6d} {size:10d}  {class_name}")

    if "--exports" in sys.argv:
        print("\n## Exports")
        for i, (class_name, name, outer, size, _) in enumerate(pkg.exports, 1):
            print(f"  [{i}] {name} : {class_name} (outer {outer}, {size} bytes)")
    if "--names" in sys.argv:
        print("\n## Name map")
        for name in pkg.names:
            print("  " + name)


if __name__ == "__main__":
    main()
