"""Lossless decode/encode of the tagged properties in cooked UE 4.26 exports (TSW7 assets are cooked with tagged,
not unversioned, properties).

decode_export() turns an export's bytes into a tree of Prop/Struct/Array/Map/... that keeps every detail needed to
write it back exactly (tag types, struct names and guids, inner types, string encodings, raw bytes of native
structs and texts). encode_export() writes it back; round-tripping an untouched export gives identical bytes.

Names are FName(text, number) values; encoding looks their text up in the package's name map, so add any new
names to the package first (names_used() lists them).
"""
import struct
from dataclasses import dataclass, field

TICKS_PER_SECOND = 10_000_000

# Native structs serialized as plain bytes (not tagged), with their sizes.
NATIVE_SIZES = {
    "Guid": 16, "Vector": 12, "Rotator": 12, "Vector2D": 8, "Vector4": 16, "Quat": 16, "LinearColor": 16,
    "Color": 4, "IntPoint": 8, "IntVector": 12, "Timespan": 8, "DateTime": 8, "FrameNumber": 4, "Box": 25,
    "Box2D": 17, "Plane": 16,
}


@dataclass(frozen=True)
class FName:
    text: str
    number: int = 0

    def __str__(self):
        return self.text if self.number == 0 else f"{self.text}_{self.number - 1}"

    @classmethod
    def parse(cls, text):
        """The FName the engine makes from a string: a numeric suffix ("WMR_350", "1F30_1") is stored as the base
        name plus number + 1, never as one literal name. A literal "WMR_350" is a different name to the engine: our
        units' layer then matched no layer, and the menu offered none of their trains."""
        head, sep, tail = text.rpartition("_")
        if sep and head and tail.isdigit() and len(tail) <= 9 and (tail == "0" or not tail.startswith("0")):
            return cls(head, int(tail) + 1)
        return cls(text, 0)


NONE = FName("None")


@dataclass
class Str:
    text: str
    form: str = "auto"  # auto | utf16 | empty-null (an empty string written as just a terminator)


@dataclass
class Text:
    raw: bytes


@dataclass
class Native:
    struct_name: str
    raw: bytes

    @property
    def ticks(self):
        return struct.unpack("<q", self.raw)[0]

    @ticks.setter
    def ticks(self, value):
        self.raw = struct.pack("<q", value)

    @property
    def seconds(self):
        return self.ticks / TICKS_PER_SECOND


@dataclass
class SoftObject:
    path: FName
    sub: Str


@dataclass
class Raw:
    raw: bytes


@dataclass
class Prop:
    name: FName
    type: FName
    value: object = None
    array_index: int = 0
    struct_name: FName = None
    struct_guid: bytes = bytes(16)
    bool_value: bool = False
    enum_name: FName = None
    inner_type: FName = None
    value_type: FName = None
    prop_guid: bytes = None


@dataclass
class Struct:
    props: list = field(default_factory=list)

    def prop(self, name):
        for p in self.props:
            if p.name.text == name:
                return p
        return None

    def __getitem__(self, name):
        p = self.prop(name)
        if p is None:
            raise KeyError(name)
        return p.bool_value if p.type.text == "BoolProperty" else p.value

    def get(self, name, default=None):
        p = self.prop(name)
        if p is None:
            return default
        return p.bool_value if p.type.text == "BoolProperty" else p.value


@dataclass
class Array:
    items: list
    inner_tag: Prop = None  # arrays of structs carry one tag describing the element struct


@dataclass
class SetValue:
    removed: list
    items: list


@dataclass
class MapValue:
    removed: list
    items: list  # [(key, value)]


@dataclass
class ExportData:
    props: Struct
    trailer: bytes  # bytes after the property list (e.g. the object's guid flag)


class DecodeError(Exception):
    pass


class _Reader:
    def __init__(self, data, pos, end):
        self.d, self.p, self.end = data, pos, end

    def take(self, fmt):
        size = struct.calcsize(fmt)
        if self.p + size > self.end:
            raise DecodeError("read past end")
        v = struct.unpack_from(fmt, self.d, self.p)
        self.p += size
        return v if len(v) > 1 else v[0]

    def raw(self, n):
        if n < 0 or self.p + n > self.end:
            raise DecodeError("read past end")
        b = self.d[self.p:self.p + n]
        self.p += n
        return bytes(b)


class Decoder:
    def __init__(self, names):
        self.names = names

    def fname(self, r):
        index, number = r.take("<ii")
        if not 0 <= index < len(self.names):
            raise DecodeError(f"bad name index {index}")
        return FName(self.names[index], number)

    def fstring(self, r):
        n = r.take("<i")
        if n == 0:
            return Str("")
        if n > 0:
            if n > 1 << 24:
                raise DecodeError("string too long")
            b = r.raw(n)
            if n == 1:
                return Str("", "empty-null")
            return Str(b[:-1].decode("latin-1"))
        b = r.raw(-2 * n)
        return Str(b[:-2].decode("utf-16-le"), "utf16")

    def text_raw(self, r):
        start = r.p
        r.take("<I")
        history = r.take("<b")
        if history == -1:
            if r.take("<i"):
                self.fstring(r)
        elif history == 0:
            for _ in range(3):
                self.fstring(r)
        else:
            raise DecodeError(f"text history {history} inside a container")
        return Text(bytes(r.d[start:r.p]))

    # -- properties --------------------------------------------------------------------------------------------
    def props(self, r):
        props = []
        while True:
            name = self.fname(r)
            if name == NONE:
                return Struct(props)
            props.append(self.prop(r, name))

    def tag(self, r, name):
        p = Prop(name, self.fname(r))
        size, p.array_index = r.take("<ii")
        t = p.type.text
        if t == "StructProperty":
            p.struct_name = self.fname(r)
            p.struct_guid = r.raw(16)
        elif t == "BoolProperty":
            p.bool_value = bool(r.take("<B"))
        elif t in ("ByteProperty", "EnumProperty"):
            p.enum_name = self.fname(r)
        elif t in ("ArrayProperty", "SetProperty"):
            p.inner_type = self.fname(r)
        elif t == "MapProperty":
            p.inner_type, p.value_type = self.fname(r), self.fname(r)
        if r.take("<B"):
            p.prop_guid = r.raw(16)
        return p, size

    def prop(self, r, name):
        p, size = self.tag(r, name)
        start = r.p
        sub = _Reader(r.d, start, start + size)
        p.value = self.value(sub, p, size)
        if sub.p != start + size:
            raise DecodeError(f"{name}: decoded {sub.p - start} of {size} bytes")
        r.p = start + size
        return p

    def value(self, r, p, size):
        t = p.type.text
        if t == "BoolProperty":
            return None
        if t == "StructProperty":
            return self.struct(r, p.struct_name.text, size)
        if t == "ArrayProperty":
            return self.array(r, p.inner_type.text)
        if t == "SetProperty":
            return self.set(r, p.inner_type.text)
        if t == "MapProperty":
            return self.map(r, p.inner_type.text, p.value_type.text)
        if t == "TextProperty":
            return Text(r.raw(size))
        if t == "ByteProperty":
            return r.take("<B") if size == 1 else self.fname(r)
        v = self.element(r, t)
        if v is _UNKNOWN:
            return Raw(r.raw(size))
        return v

    def element(self, r, t):
        if t in _NUMERIC:
            return r.take(_NUMERIC[t])
        if t in ("NameProperty", "EnumProperty"):
            return self.fname(r)
        if t == "StrProperty":
            return self.fstring(r)
        if t in ("ObjectProperty", "ClassProperty", "WeakObjectProperty", "InterfaceProperty"):
            return r.take("<i")
        if t in ("SoftObjectProperty", "SoftClassProperty"):
            return SoftObject(self.fname(r), self.fstring(r))
        if t == "LazyObjectProperty":
            return Raw(r.raw(16))
        if t == "TextProperty":
            return self.text_raw(r)
        return _UNKNOWN

    def struct(self, r, struct_name, size):
        """A struct value whose byte size is known: tagged if that parses exactly, else native bytes."""
        if struct_name in NATIVE_SIZES or struct_name.endswith("Quantity"):
            return Native(struct_name, r.raw(size))
        start = r.p
        try:
            s = self.props(r)
            if r.p == start + size:
                return s
        except (DecodeError, struct.error):
            pass
        r.p = start
        return Native(struct_name, r.raw(size))

    def array(self, r, inner):
        count = r.take("<i")
        if inner == "StructProperty":
            tag, size = self.tag(r, self.fname(r))
            start, end = r.p, r.p + size
            name = tag.struct_name.text
            items = None
            if name not in NATIVE_SIZES and not name.endswith("Quantity"):
                try:
                    items = [self.props(r) for _ in range(count)]
                    if r.p != end:
                        items = None
                except (DecodeError, struct.error):
                    items = None
            if items is None:
                r.p = start
                each = size // count if count else 0
                if each * count != size:
                    raise DecodeError(f"array of {name}: {size} bytes for {count} items")
                items = [Native(name, r.raw(each)) for _ in range(count)]
            return Array(items, tag)
        if inner == "ByteProperty" and r.end - r.p == count:
            return Array([r.take("<B") for _ in range(count)])
        items = []
        for _ in range(count):
            v = self.element(r, inner) if inner != "BoolProperty" else bool(r.take("<B"))
            if v is _UNKNOWN:
                raise DecodeError(f"array of {inner}")
            items.append(v)
        return Array(items)

    def container_item(self, r, t, is_key):
        if t == "StructProperty":
            # Map/set tags don't name the struct: keys are Guids in these assets, values tagged structs.
            return Native("Guid", r.raw(16)) if is_key else self.props(r)
        if t == "BoolProperty":
            return bool(r.take("<B"))
        if t == "ByteProperty":
            return r.take("<B")
        v = self.element(r, t)
        if v is _UNKNOWN:
            raise DecodeError(f"container of {t}")
        return v

    def set(self, r, inner):
        removed = [self.container_item(r, inner, True) for _ in range(r.take("<i"))]
        items = [self.container_item(r, inner, True) for _ in range(r.take("<i"))]
        return SetValue(removed, items)

    def map(self, r, key_type, value_type):
        removed = [self.container_item(r, key_type, True) for _ in range(r.take("<i"))]
        items = []
        for _ in range(r.take("<i")):
            k = self.container_item(r, key_type, True)
            items.append((k, self.container_item(r, value_type, False)))
        return MapValue(removed, items)


_UNKNOWN = object()
_NUMERIC = {
    "Int8Property": "<b", "Int16Property": "<h", "IntProperty": "<i", "Int64Property": "<q",
    "UInt16Property": "<H", "UInt32Property": "<I", "UInt64Property": "<Q",
    "FloatProperty": "<f", "DoubleProperty": "<d",
}


class Encoder:
    def __init__(self, name_index):
        self.name_index = name_index  # text -> index in the package's name map

    def fname(self, n):
        return struct.pack("<ii", self.name_index[n.text], n.number)

    @staticmethod
    def fstring(s):
        if s.form == "empty-null":
            return struct.pack("<i", 1) + b"\0"
        if s.text == "" and s.form == "auto":
            return struct.pack("<i", 0)
        if s.form == "utf16" or any(ord(c) > 0xFF for c in s.text):
            data = s.text.encode("utf-16-le") + b"\0\0"
            return struct.pack("<i", -(len(data) // 2)) + data
        data = s.text.encode("latin-1") + b"\0"
        return struct.pack("<i", len(data)) + data

    def props(self, s):
        out = bytearray()
        for p in s.props:
            out += self.prop(p)
        out += self.fname(NONE)
        return bytes(out)

    def tag(self, p, size):
        out = bytearray(self.fname(p.name) + self.fname(p.type) + struct.pack("<ii", size, p.array_index))
        t = p.type.text
        if t == "StructProperty":
            out += self.fname(p.struct_name) + p.struct_guid
        elif t == "BoolProperty":
            out += struct.pack("<B", 1 if p.bool_value else 0)
        elif t in ("ByteProperty", "EnumProperty"):
            out += self.fname(p.enum_name)
        elif t in ("ArrayProperty", "SetProperty"):
            out += self.fname(p.inner_type)
        elif t == "MapProperty":
            out += self.fname(p.inner_type) + self.fname(p.value_type)
        out += (b"\1" + p.prop_guid) if p.prop_guid is not None else b"\0"
        return bytes(out)

    def prop(self, p):
        value = self.value(p)
        return self.tag(p, len(value)) + value

    def value(self, p):
        t = p.type.text
        v = p.value
        if t == "BoolProperty":
            return b""
        if t == "ArrayProperty":
            return self.array(v, p.inner_type.text)
        if t == "SetProperty":
            return self.container(v.removed, p.inner_type.text) + self.container(v.items, p.inner_type.text)
        if t == "MapProperty":
            out = self.container(v.removed, p.inner_type.text)
            out += struct.pack("<i", len(v.items))
            for k, val in v.items:
                out += self.item(k, p.inner_type.text) + self.item(val, p.value_type.text)
            return out
        if t == "ByteProperty" and isinstance(v, int):
            return struct.pack("<B", v)
        return self.item(v, t)

    def item(self, v, t):
        if isinstance(v, Struct):
            return self.props(v)
        if isinstance(v, (Native, Text, Raw)):
            return v.raw
        if isinstance(v, FName):
            return self.fname(v)
        if isinstance(v, Str):
            return self.fstring(v)
        if isinstance(v, SoftObject):
            return self.fname(v.path) + self.fstring(v.sub)
        if isinstance(v, bool):
            return struct.pack("<B", 1 if v else 0)
        if t in _NUMERIC:
            return struct.pack(_NUMERIC[t], v)
        if t in ("ObjectProperty", "ClassProperty", "WeakObjectProperty", "InterfaceProperty"):
            return struct.pack("<i", v)
        if t == "ByteProperty":
            return struct.pack("<B", v)
        raise TypeError(f"can't encode {type(v).__name__} as {t}")

    def container(self, items, t):
        return struct.pack("<i", len(items)) + b"".join(self.item(i, t) for i in items)

    def array(self, a, inner):
        out = bytearray(struct.pack("<i", len(a.items)))
        if inner == "StructProperty":
            body = b"".join(self.item(i, inner) for i in a.items)
            out += self.tag(a.inner_tag, len(body)) + body
            return bytes(out)
        for i in a.items:
            out += self.item(i, inner)
        return bytes(out)


def timespan_value_offsets(data, name_index, field_name):
    """Offsets of the 8-byte values of every tagged `field_name` Timespan property in encoded bytes (found by
    their exact tag bytes), so copies can be re-timed by patching instead of re-encoding."""
    prefix = struct.pack("<iiiiiiii", name_index[field_name], 0, name_index["StructProperty"], 0, 8, 0,
                         name_index["Timespan"], 0) + bytes(16) + b"\0"
    offsets, pos = [], data.find(prefix)
    while pos >= 0:
        offsets.append(pos + len(prefix))
        pos = data.find(prefix, pos + 1)
    return offsets


def decode_export(pkg, data):
    """Decode one export's bytes (from EditablePackage.export_data) into ExportData."""
    r = _Reader(data, 0, len(data))
    props = Decoder(pkg.names).props(r)
    return ExportData(props, bytes(data[r.p:]))


def encode_export(pkg, export):
    return Encoder(pkg._name_index()).props(export.props) + export.trailer


def names_used(node, out=None):
    """Every name text a decoded tree refers to (to add to a package before encoding)."""
    out = set() if out is None else out
    if isinstance(node, FName):
        out.add(node.text)
    elif isinstance(node, Prop):
        for n in (node.name, node.type, node.struct_name, node.enum_name, node.inner_type, node.value_type):
            if n is not None:
                out.add(n.text)
        names_used(node.value, out)
    elif isinstance(node, Struct):
        out.add("None")
        for p in node.props:
            names_used(p, out)
    elif isinstance(node, Array):
        if node.inner_tag is not None:
            names_used(node.inner_tag, out)
        for i in node.items:
            names_used(i, out)
    elif isinstance(node, (SetValue, MapValue)):
        for i in node.removed + node.items:
            names_used(i, out)
    elif isinstance(node, tuple):
        for i in node:
            names_used(i, out)
    elif isinstance(node, SoftObject):
        out.add(node.path.text)
    elif isinstance(node, ExportData):
        names_used(node.props, out)
    return out
