"""Build a mod that adds its own copy of a route's timetable, as a separate timetable in the game's menu.

The copy lives in the mod's own plugin (/<ModName>/Timetable/<Object>), with its own name and description, and
whole services can be shifted in time. Layers whose services change get their data track copied into the mod with
the same shift; every other layer keeps using the route's own data track. A ContentManifest in the mod lists the
timetable, which is how the game finds it.

Usage:
  python build_timetable_mod.py --extracted extracted --out build/TimetableToday.pak --mod TimetableToday
      --timetable TS2Prototype/Plugins/DLC/AABS_TrentValley_Gameplay/Content/Timetable/WCMLTT.uasset
      --manifest-template TS2Prototype/Plugins/DLC/WCMLTrentValley/Content/ContentManifest.uasset
      --depends WCMLTrentValleyCreweMiltonKeynes
      --title "..." --description "..." --shift 1U00=7 1U00_AI=7
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assetregistry import Registry  # noqa: E402
from shift_services import clock_offsets, patch  # noqa: E402
from uasset_dump import TICKS_PER_SECOND, load  # noqa: E402
from uasset_edit import EditablePackage, fstring_bytes  # noqa: E402

GAME_PATH_ROOT = "TS2Prototype/Plugins/DLC/"


def package_path(rel):
    """TS2Prototype/Plugins/DLC/<Plugin>/Content/A/B.uasset -> /<Plugin>/A/B"""
    plugin, rest = rel[len(GAME_PATH_ROOT):].split("/Content/", 1)
    return f"/{plugin}/{rest[:-len('.uasset')]}"


def top_level_tags(pkg, data):
    """(name, type, value_start, size, size_field_pos, bool_pos) for each top-level tagged property of an export."""
    tags, pos = [], 0
    while True:
        index, number = struct.unpack_from("<ii", data, pos)
        name = pkg.names[index]
        if name == "None":
            return tags
        ptype = pkg.names[struct.unpack_from("<i", data, pos + 8)[0]]
        size_pos = pos + 16
        size = struct.unpack_from("<i", data, size_pos)[0]
        pos += 24
        bool_pos = None
        if ptype == "StructProperty":
            pos += 8 + 16
        elif ptype == "BoolProperty":
            bool_pos = pos
            pos += 1
        elif ptype in ("ByteProperty", "EnumProperty", "ArrayProperty", "SetProperty"):
            pos += 8
        elif ptype == "MapProperty":
            pos += 16
        if data[pos]:
            pos += 16
        pos += 1
        tags.append((name, ptype, pos, size, size_pos, bool_pos))
        pos += size


def text_value(key, text):
    """An FText with Base history: no namespace, a key no localisation table has, so the source string shows."""
    return struct.pack("<Ib", 0, 0) + fstring_bytes("") + fstring_bytes(key) + fstring_bytes(text)


def set_properties(pkg, bools=None, texts=None):
    data = bytearray(pkg.export_data(1))
    tags = {t[0]: t for t in top_level_tags(pkg, data)}
    for name, value in (bools or {}).items():
        data[tags[name][5]] = 1 if value else 0
    # Rewrite texts from the back so earlier positions stay valid.
    for name, (key, text) in sorted((texts or {}).items(), key=lambda kv: -tags[kv[0]][2]):
        _, _, start, size, size_pos, _ = tags[name]
        value = text_value(key, text)
        data[start:start + size] = value
        struct.pack_into("<i", data, size_pos, len(value))
    pkg.replace_export_data(1, bytes(data))


def shift_services(uasset_path, wanted, map_key=None):
    """Body bytes of uasset_path with every clock time of the wanted services shifted (wanted: name -> minutes)."""
    exports, _, body = load(uasset_path, with_offsets=True)
    props = exports[0]["properties"]
    by_delta = {}
    if map_key:
        for name, track in props.get(map_key, []):
            if name in wanted:
                by_delta.setdefault(wanted[name], []).extend(clock_offsets(track, []))
    else:
        for service in props["Services"]:
            if service["Name"] in wanted:
                by_delta.setdefault(wanted[service["Name"]], []).extend(clock_offsets(service, []))
    for minutes, offsets in by_delta.items():
        body = patch(body, offsets, int(minutes * 60 * TICKS_PER_SECOND))
    return body, props


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extracted", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mod", required=True)
    ap.add_argument("--timetable", required=True)
    ap.add_argument("--manifest-template", required=True)
    ap.add_argument("--registry-template", required=True,
                    help="the route's AssetRegistry.bin, whose entry for the timetable is copied")
    ap.add_argument("--depends", nargs="*", default=[])
    ap.add_argument("--object-name", help="name of the timetable copy (default <original>_<mod>)")
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--shift", nargs="*", default=[], help="SERVICE=MINUTES")
    args = ap.parse_args()

    wanted = {k: float(v) for k, v in (s.split("=", 1) for s in args.shift)}
    src = os.path.join(args.extracted, args.timetable)
    original_path = package_path(args.timetable)
    original_object = original_path.rsplit("/", 1)[1]
    new_object = args.object_name or f"{original_object}_{args.mod}"
    new_path = f"/{args.mod}/Timetable/{new_object}"
    plugin_content = f"{GAME_PATH_ROOT}{args.mod}/Content/"

    with tempfile.TemporaryDirectory() as staging:
        def out_path(rel):
            path = os.path.join(staging, plugin_content, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return path

        # 1. Timetable copy: shift services, then rename it and point changed layers at the mod's data tracks.
        body, props = shift_services(src, wanted)
        layers_changed = {s["LayerName"] for s in props["Services"] if s["Name"] in wanted}
        tt = EditablePackage.load(src)
        tt.body[:] = body
        renames = {original_path: new_path, original_object: new_object}
        datatracks = []
        for layer in props.get("Layers", []):
            track = layer.get("DataTrack")
            if layer["Name"] in layers_changed and track and track != "None":
                track_package, track_object = track.split(".", 1)
                new_track = f"/{args.mod}/Timetable/DataTracks/{track_object}"
                renames[track_package] = new_track
                renames[track] = f"{new_track}.{track_object}"
                datatracks.append((track_package, new_track))
        if "None" in layers_changed or not props.get("Layers"):
            sys.exit("services outside layers would need the master data track copied; not supported yet")
        set_properties(tt, bools={"bDefaultTimetable": False}, texts={
            "TimetableName": (f"{args.mod}_Name", args.title),
            "TimetableDescription": (f"{args.mod}_Description", args.description or args.title),
        })
        tt.rename_names(renames)
        tt.save(out_path(f"Timetable/{new_object}.uasset"))
        print(f"timetable {new_path}.{new_object}: {len(wanted)} services shifted, layers {sorted(layers_changed)}")

        # 2. Data tracks of the changed layers, shifted the same way and moved into the mod.
        for track_package, new_track in datatracks:
            rel = f"{GAME_PATH_ROOT}{track_package.split('/')[1]}/Content/{track_package.split('/', 2)[2]}.uasset"
            body, _ = shift_services(os.path.join(args.extracted, rel), wanted, map_key="ServiceDataTracks")
            dt = EditablePackage.load(os.path.join(args.extracted, rel))
            dt.body[:] = body
            dt.rename_names({track_package: new_track})
            dt.save(out_path(f"Timetable/DataTracks/{new_track.rsplit('/', 1)[1]}.uasset"))
            print(f"data track {new_track}")

        # 3. ContentManifest listing just the new timetable.
        manifest = EditablePackage.load(os.path.join(args.extracted, args.manifest_template))
        template_path = package_path(args.manifest_template)
        timetable_ref = f"{new_path}.{new_object}"
        reuse = next(n for n in manifest.names if n.endswith(f".{original_object}"))
        manifest.rename_names({template_path: f"/{args.mod}/ContentManifest", reuse: timetable_ref,
                               reuse.split(".")[0]: new_path})
        n = manifest.name_index
        value = struct.pack("<i", 1) + struct.pack("<ii", n(timetable_ref), 0) + fstring_bytes("")
        data = struct.pack("<iiiiii", n("DefinedTimetables_PC"), 0, n("ArrayProperty"), 0, len(value), 0)
        data += struct.pack("<ii", n("SoftObjectProperty"), 0) + b"\0" + value
        data += struct.pack("<ii", n("None"), 0) + struct.pack("<i", 0)
        manifest.replace_export_data(1, data)
        manifest.save(out_path("ContentManifest.uasset"))
        print(f"manifest /{args.mod}/ContentManifest -> {timetable_ref}")

        # 4. AssetRegistry.bin: the game learns which timetables a content pack has (and their menu details)
        # from this, without loading them. One entry, copied from the original timetable's.
        template = Registry.load(os.path.join(args.extracted, args.registry_template))
        original_ref = f"{original_path}.{original_object}"
        entry = dict(next(a for a in template.assets if a["object_path"] == original_ref))

        def loctext(key, text):
            return 'NSLOCTEXT("", "{}", "{}")'.format(key, text.replace("\\", "\\\\").replace('"', '\\"'))

        overrides = {
            "TimetableName": loctext(f"{args.mod}_Name", args.title),
            "TimetableDescription": loctext(f"{args.mod}_Description", args.description or args.title),
            "bDefaultTimetable": "False",
        }
        entry.update(object_path=timetable_ref, package_path=new_path.rsplit("/", 1)[0], package_name=new_path,
                     asset_name=new_object, tags=[(k, overrides.get(k, v)) for k, v in entry["tags"]])
        registry = Registry()
        registry.version = template.version
        registry.assets = [entry]
        registry.depends_raw = template.body_after_assets
        registry.save(out_path("AssetRegistry.bin"))
        print(f"asset registry: {timetable_ref} ({len(entry['tags'])} tags)")

        cmd = [sys.executable, "-I", os.path.join(os.path.dirname(__file__), "make_mod.py"), "--name", args.mod,
               "--files", staging, "--out", args.out, "--depends", *args.depends]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
