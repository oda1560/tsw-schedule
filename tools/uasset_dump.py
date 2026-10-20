"""Dump the tagged properties of every export in a cooked UE 4.26 .uasset/.uexp pair to JSON.

Usage: python uasset_dump.py <file.uasset> <out.json>
Only works for packages saved with tagged (not unversioned) properties.
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uasset_info import Package, Reader  # noqa: E402

TICKS_PER_SECOND = 10_000_000

# Structs that UE serializes natively instead of as tagged properties.
NATIVE_STRUCTS = {
    "Guid": lambda r: r.read(16).hex(),
    "Vector": lambda r: struct.unpack("<3f", r.read(12)),
    "Rotator": lambda r: struct.unpack("<3f", r.read(12)),
    "Vector2D": lambda r: struct.unpack("<2f", r.read(8)),
    "Quat": lambda r: struct.unpack("<4f", r.read(16)),
    "LinearColor": lambda r: struct.unpack("<4f", r.read(16)),
    "Color": lambda r: r.read(4).hex(),
    "IntPoint": lambda r: struct.unpack("<2i", r.read(8)),
    "Timespan": lambda r: {"timespan_s": r.i64() / TICKS_PER_SECOND},
    "DateTime": lambda r: {"datetime_ticks": r.i64()},
}


class Dumper:
    def __init__(self, pkg, body_start=None):
        """body_start: length of the .uasset; when given, each Timespan also records "@", its offset in the .uexp."""
        self.pkg = pkg
        self.body_start = body_start

    def name(self, r):
        return self.pkg.fname(r)

    def obj_ref(self, index):
        if index == 0:
            return None
        if index < 0:
            return "import:" + self.pkg.resolve(index)
        return f"export:{index}:{self.pkg.exports[index - 1][1]}"

    def properties(self, r, end):
        out = {}
        while r.pos < end:
            name = self.name(r)
            if name == "None":
                break
            ptype = self.name(r)
            size, array_index = r.i32(), r.i32()
            tag = {}
            if ptype == "StructProperty":
                tag["struct"] = self.name(r)
                r.read(16)
            elif ptype == "BoolProperty":
                tag["bool"] = bool(r.read(1)[0])
            elif ptype in ("ByteProperty", "EnumProperty"):
                tag["enum"] = self.name(r)
            elif ptype in ("ArrayProperty", "SetProperty"):
                tag["inner"] = self.name(r)
            elif ptype == "MapProperty":
                tag["inner"], tag["value"] = self.name(r), self.name(r)
            if r.read(1)[0]:
                r.read(16)
            start = r.pos
            try:
                value = self.value(r, ptype, tag, size)
            except Exception as e:  # keep going: record what could not be decoded
                value = {"undecoded": ptype, "size": size, "error": str(e)}
            r.pos = start + size
            key = name if array_index == 0 else f"{name}[{array_index}]"
            out[key] = value
        return out

    def value(self, r, ptype, tag, size):
        if ptype == "BoolProperty":
            return tag["bool"]
        if ptype == "IntProperty":
            return r.i32()
        if ptype == "Int64Property":
            return r.i64()
        if ptype == "UInt32Property":
            return r.u32()
        if ptype == "FloatProperty":
            return struct.unpack("<f", r.read(4))[0]
        if ptype == "DoubleProperty":
            return struct.unpack("<d", r.read(8))[0]
        if ptype in ("NameProperty", "EnumProperty"):
            return self.name(r)
        if ptype == "ByteProperty":
            return r.read(1)[0] if size == 1 else self.name(r)
        if ptype == "StrProperty":
            return r.fstring()
        if ptype == "TextProperty":
            return self.text(r, size)
        if ptype == "ObjectProperty":
            return self.obj_ref(r.i32())
        if ptype == "SoftObjectProperty":
            path = self.name(r)
            sub = r.fstring()
            return path + (":" + sub if sub else "")
        if ptype == "StructProperty":
            return self.struct(r, tag["struct"], r.pos + size)
        if ptype in ("ArrayProperty", "SetProperty"):
            return self.array(r, tag["inner"], r.pos + size, is_set=ptype == "SetProperty")
        if ptype == "MapProperty":
            return self.map(r, tag["inner"], tag["value"], r.pos + size)
        return {"undecoded": ptype, "size": size}

    def text(self, r, size):
        start = r.pos
        r.u32()  # flags
        history = struct.unpack("<b", r.read(1))[0]
        if history == -1:  # culture invariant / none
            if r.i32():
                return r.fstring()
            return ""
        if history == 0:  # base: namespace, key, source string
            r.fstring(); r.fstring()
            return r.fstring()
        r.pos = start + size
        return {"text_history": history}

    def struct(self, r, struct_name, end):
        if struct_name == "Timespan" and self.body_start is not None:
            offset = r.pos - self.body_start
            return {"timespan_s": r.i64() / TICKS_PER_SECOND, "@": offset}
        if struct_name in NATIVE_STRUCTS:
            return NATIVE_STRUCTS[struct_name](r)
        if struct_name.endswith("Quantity") and end - r.pos == 4:
            # TSW unit types (DistanceQuantity, SpeedQuantity, ...) serialize as one float in SI units.
            return {struct_name: struct.unpack("<f", r.read(4))[0]}
        return self.properties(r, end)

    def element(self, r, inner, end, struct_name=None):
        if inner == "StructProperty":
            return self.struct(r, struct_name, end)
        if inner == "BoolProperty":
            return bool(r.read(1)[0])
        if inner == "ByteProperty":
            return r.read(1)[0]
        return self.value(r, inner, {}, 0)

    def array(self, r, inner, end, is_set=False):
        if is_set:
            for _ in range(r.i32()):  # elements to remove
                self.element(r, inner, end)
        count = r.i32()
        if inner == "StructProperty" and not is_set:
            # Arrays of structs carry one inner tag describing the element struct.
            self.name(r); self.name(r); r.i32(); r.i32()
            struct_name = self.name(r)
            r.read(16)
            if r.read(1)[0]:
                r.read(16)
            if struct_name in NATIVE_STRUCTS:
                return [NATIVE_STRUCTS[struct_name](r) for _ in range(count)]
            return [self.properties(r, end) for _ in range(count)]
        if inner == "ByteProperty" and count and (end - r.pos) == count:
            return list(r.read(count))
        return [self.element(r, inner, end) for _ in range(count)]

    def map(self, r, key_type, value_type, end):
        for _ in range(r.i32()):  # keys to remove
            self.map_item(r, key_type, end, is_key=True)
        out = []
        for _ in range(r.i32()):
            k = self.map_item(r, key_type, end, is_key=True)
            v = self.map_item(r, value_type, end, is_key=False)
            out.append([k, v])
        return out

    def map_item(self, r, ptype, end, is_key):
        # Map tags do not name the struct type: assume a Guid key and tagged-property values.
        if ptype == "StructProperty":
            return r.read(16).hex() if is_key else self.properties(r, end)
        return self.element(r, ptype, end)


def load(uasset_path, with_offsets=False):
    """Decode every export of a .uasset/.uexp pair. Returns (exports, header bytes, body bytes)."""
    with open(uasset_path, "rb") as f:
        header = f.read()
    with open(uasset_path[:-len(".uasset")] + ".uexp", "rb") as f:
        body = f.read()
    pkg = Package(header)
    data = header + body
    dumper = Dumper(pkg, len(header) if with_offsets else None)
    result = []
    for i, (class_name, name, outer, size, offset) in enumerate(pkg.exports, 1):
        r = Reader(data, offset)
        result.append({
            "index": i,
            "name": name,
            "class": class_name,
            "properties": dumper.properties(r, offset + size),
        })
    return result, header, body


def main():
    uasset_path, out_path = sys.argv[1], sys.argv[2]
    result, _, _ = load(uasset_path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    print(f"wrote {out_path}: {len(result)} exports")


if __name__ == "__main__":
    main()
