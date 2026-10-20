"""Recorded runs (data tracks) handled as raw bytes: slice a service's recording out of a layer's data track and
write re-timed copies into a copy of the same package (same name table, so the bytes stay valid).

A data track export is: VersionNumber (int), JunctionDataTrack (array, optional), ServiceDataTracks (map of
service name -> tagged struct with TrackData[] {Time, Location, ...}), None, trailer. Dovetail's tracks run to
hundreds of MB, so nothing here builds Python objects per track point.
"""
import struct

from .package import EditablePackage
from .props import timespan_value_offsets


def _fname(data, pos, names):
    index, number = struct.unpack_from("<ii", data, pos)
    return names[index], number


def _skip_tag(data, pos, names):
    """At a property tag: return (name, type, tag_start, value_start, value_end) or None at the None terminator."""
    start = pos
    name, _ = _fname(data, pos, names)
    if name == "None":
        return None
    ptype, _ = _fname(data, pos + 8, names)
    size = struct.unpack_from("<i", data, pos + 16)[0]
    pos += 24
    if ptype == "StructProperty":
        pos += 8 + 16
    elif ptype == "BoolProperty":
        pos += 1
    elif ptype in ("ByteProperty", "EnumProperty", "ArrayProperty", "SetProperty"):
        pos += 8
    elif ptype == "MapProperty":
        pos += 16
    has_guid = data[pos]
    pos += 1 + (16 if has_guid else 0)
    return name, ptype, start, pos, pos + size


def top_level(data, names):
    """{property name: (tag_start, value_start, value_end)} and the offset of the None terminator."""
    props, pos = {}, 0
    while True:
        tag = _skip_tag(data, pos, names)
        if tag is None:
            return props, pos
        name, _, start, value_start, end = tag
        props[name] = (start, value_start, end)
        pos = end


def struct_end(data, pos, names):
    """End offset of a tagged struct starting at pos."""
    while True:
        tag = _skip_tag(data, pos, names)
        if tag is None:
            return pos + 8
        pos = tag[4]


class LayerTrack:
    """One data track package, indexed by service name (no decoding of the recordings)."""

    def __init__(self, path):
        self.path = path
        pkg = EditablePackage.load(path)
        self.names = list(pkg.names)
        self.name_index = dict(pkg._name_index())
        self.data = pkg.export_data(1)
        self.props, self.none_at = top_level(self.data, self.names)
        self.entries = {}
        if "ServiceDataTracks" in self.props:
            _, value_start, value_end = self.props["ServiceDataTracks"]
            removed, count = struct.unpack_from("<ii", self.data, value_start)
            if removed:
                raise ValueError("data track map with removed keys")
            pos = value_start + 8
            for _ in range(count):
                name, number = _fname(self.data, pos, self.names)
                key = name if number == 0 else f"{name}_{number - 1}"
                end = struct_end(self.data, pos + 8, self.names)
                self.entries[key] = (pos + 8, end)
                pos = end
            if pos != value_end:
                raise ValueError("data track map size mismatch")
        self._offsets = {}

    def recording(self, service, delta_ticks):
        """The service's recording with every set Time moved by delta_ticks."""
        start, end = self.entries[service]
        raw = self.data[start:end]
        if service not in self._offsets:
            self._offsets[service] = timespan_value_offsets(raw, self.name_index, "Time")
        out = bytearray(raw)
        for off in self._offsets[service]:
            ticks = struct.unpack_from("<q", out, off)[0]
            if ticks > 0:
                struct.pack_into("<q", out, off, ticks + delta_ticks)
        return bytes(out)


def write_track(source, recordings, out_path, map_tag_source=None):
    """Write a copy of `source` (a LayerTrack) holding exactly `recordings`: [(service name, bytes from
    source.recording())]. No junction recording is kept (it belongs to the original day). `map_tag_source` gives a
    ServiceDataTracks tag to copy when the source has none (e.g. the master track)."""
    pkg = EditablePackage.load(source.path)
    pkg.add_names([name for name, _ in recordings] + ["None"])
    index = pkg._name_index()
    data = source.data
    body = bytearray()
    if "VersionNumber" in source.props:
        start, _, end = source.props["VersionNumber"]
        body += data[start:end]
    if recordings:
        value = bytearray(struct.pack("<ii", 0, len(recordings)))
        for name, rec in recordings:
            value += struct.pack("<ii", index[name], 0) + rec
        if "ServiceDataTracks" in source.props:
            tag_start, value_start, _ = source.props["ServiceDataTracks"]
            tag = bytearray(data[tag_start:value_start])
        else:
            tag = bytearray(map_tag_source)
        struct.pack_into("<i", tag, 16, len(value))
        body += tag + value
    body += struct.pack("<ii", index["None"], 0)
    body += data[source.none_at + 8:]  # trailer after the property list
    pkg.replace_export_data(1, bytes(body))
    pkg.save(out_path)
