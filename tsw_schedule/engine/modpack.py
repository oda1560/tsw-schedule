"""Package a generated timetable as a TSW7 mod that adds it as its own timetable on the route.

The mod is a plugin of its own (/<Mod>/...): the timetable and its data track moved into it, a ContentManifest
listing the timetable, an AssetRegistry.bin entry (what makes the game see it; copied from the route timetable's
entry with our name/description), and .uplugin/.upluginmanifest/.dlc descriptors whose Name matches the pak file
name (or the game skips the pak). Install by copying the pak to Documents\\My Games\\TrainSimWorld7\\Saved\\UserContent
(with mods enabled in the game's settings).
"""
import json
import os
import shutil
import struct
import tempfile

from .assetregistry import Registry
from .package import EditablePackage, fstring_bytes
from .pak import build as build_pak

GAME_PATH_ROOT = "TS2Prototype/Plugins/DLC/"


def package_path(rel):
    """TS2Prototype/Plugins/DLC/<Plugin>/Content/A/B.uasset -> /<Plugin>/A/B"""
    plugin, rest = rel[len(GAME_PATH_ROOT):].split("/Content/", 1)
    return f"/{plugin}/{rest[:-len('.uasset')]}"


def rel_path(package):
    """/<Plugin>/A/B -> TS2Prototype/Plugins/DLC/<Plugin>/Content/A/B.uasset"""
    plugin, rest = package.lstrip("/").split("/", 1)
    return f"{GAME_PATH_ROOT}{plugin}/Content/{rest}.uasset"


def _descriptor(name, description):
    return {
        "FileVersion": 3, "Version": 1, "VersionName": "1.0", "FriendlyName": name, "Description": description,
        "Category": "User.Content", "CreatedBy": "tsw-schedule", "CreatedByURL": "", "DocsURL": "",
        "MarketplaceURL": "", "SupportURL": "", "EnabledByDefault": True, "CanContainContent": True,
    }


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent="\t")


def _loctext(key, value):
    return 'NSLOCTEXT("", "{}", "{}")'.format(key, value.replace("\\", "\\\\").replace('"', '\\"'))


class ModNames:
    """Where things live inside the mod plugin."""

    def __init__(self, mod, object_name):
        self.mod = mod
        self.timetable_object = object_name
        self.timetable = f"/{mod}/Timetable/{object_name}"

    @property
    def timetable_ref(self):
        return f"{self.timetable}.{self.timetable_object}"

    def track(self, original_package):
        """A data track moves into the mod under its own object name."""
        return f"/{self.mod}/Timetable/DataTracks/{original_package.rsplit('/', 1)[1]}"

    def track_ref(self, original_package):
        new = self.track(original_package)
        return f"{new}.{new.rsplit('/', 1)[1]}"


def build_mod(*, extracted, route, names, timetable_file, tracks, title, description, out_pak):
    """route: dict with timetable, manifest_template, registry_template (paths relative to the extracted pak tree)
    and dlc (the route's content name, for DLCDependencies). tracks: [(original package path, file)]; the
    timetable must already refer to names.track_ref(original) for each."""
    original_tt = package_path(route["timetable"])
    original_tt_object = original_tt.rsplit("/", 1)[1]

    with tempfile.TemporaryDirectory() as staging:
        def out(package):
            path = os.path.join(staging, rel_path(package))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return path

        tt = EditablePackage.load(timetable_file)
        tt.rename_names({original_tt: names.timetable, original_tt_object: names.timetable_object})
        tt.save(out(names.timetable))

        for original, path in tracks:
            track = EditablePackage.load(path)
            track.rename_names({original: names.track(original)})
            track.save(out(names.track(original)))

        # ContentManifest listing just our timetable (built from the route's own manifest).
        manifest = EditablePackage.load(os.path.join(extracted, route["manifest_template"]))
        reuse = next(n for n in manifest.names if n.endswith(f".{original_tt_object}"))
        manifest.rename_names({package_path(route["manifest_template"]): f"/{names.mod}/ContentManifest",
                               reuse: names.timetable_ref, reuse.split(".")[0]: names.timetable})
        n = manifest.name_index
        value = struct.pack("<i", 1) + struct.pack("<ii", n(names.timetable_ref), 0) + fstring_bytes("")
        data = struct.pack("<iiiiii", n("DefinedTimetables_PC"), 0, n("ArrayProperty"), 0, len(value), 0)
        data += struct.pack("<ii", n("SoftObjectProperty"), 0) + b"\0" + value
        data += struct.pack("<ii", n("None"), 0) + struct.pack("<i", 0)
        manifest.replace_export_data(1, data)
        manifest.save(out(f"/{names.mod}/ContentManifest"))

        # AssetRegistry.bin: one entry for our timetable, from the route timetable's entry.
        template = Registry.load(os.path.join(extracted, route["registry_template"]))
        entry = dict(next(a for a in template.assets
                          if a["object_path"] == f"{original_tt}.{original_tt_object}"))
        overrides = {"TimetableName": _loctext(f"{names.mod}_Name", title),
                     "TimetableDescription": _loctext(f"{names.mod}_Description", description),
                     "bDefaultTimetable": "False"}
        entry.update(object_path=names.timetable_ref, package_path=names.timetable.rsplit("/", 1)[0],
                     package_name=names.timetable, asset_name=names.timetable_object,
                     tags=[(k, overrides.get(k, v)) for k, v in entry["tags"]])
        registry = Registry()
        registry.version = template.version
        registry.assets = [entry]
        registry.depends_raw = template.body_after_assets
        registry.save(os.path.join(staging, GAME_PATH_ROOT, names.mod, "Content", "AssetRegistry.bin"))

        # Plugin descriptors.
        plugins = os.path.join(staging, "TS2Prototype", "Plugins")
        _write_json(os.path.join(plugins, "DLC", names.mod, f"{names.mod}.uplugin"), _descriptor(names.mod, title))
        _write_json(os.path.join(plugins, f"{names.mod}.upluginmanifest"), {"Contents": [{
            "File": f"../../../TS2Prototype/Plugins/DLC/{names.mod}/{names.mod}.uplugin",
            "Descriptor": _descriptor(names.mod, title)}]})
        _write_json(os.path.join(plugins, "DLC", names.mod, "Content", f"{names.mod}.dlc"), {
            "ContentPath": f"Plugins/DLC/{names.mod}/Content/", "DLCDependencies": [route["dlc"]],
            "EpicStoreItemId": None, "Files": [], "GDKStoreId": None, "LiveProductId": None,
            "MountPoint": f"/{names.mod}/", "Name": names.mod, "PS5ContentId": None, "PSNContentId": None,
            "SteamAppId": None, "UWPProductId": None})

        if os.path.basename(out_pak) != f"{names.mod}.pak":
            raise ValueError("the pak file must be named after the mod, or the game ignores it")
        os.makedirs(os.path.dirname(os.path.abspath(out_pak)), exist_ok=True)
        build_pak(staging, out_pak)
    return out_pak


def install(pak, user_content):
    os.makedirs(user_content, exist_ok=True)
    dest = os.path.join(user_content, os.path.basename(pak))
    shutil.copyfile(pak, dest)
    return dest
