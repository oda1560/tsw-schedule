"""Wrap patched game files into a TSW mod pak: a small plugin of its own (.uplugin, .upluginmanifest, .dlc) plus the
patched files at the paths they override.

Usage: python make_mod.py --name <ModName> --files <staging dir> --out <out.pak>
                          [--depends <DLC name> ...] [--steam-app-id <id>|null]
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pakbuild import build  # noqa: E402


def descriptor(name):
    return {
        "FileVersion": 3,
        "Version": 1,
        "VersionName": "1.0",
        "FriendlyName": name,
        "Description": name,
        "Category": "User.Content",
        "CreatedBy": "",
        "CreatedByURL": "",
        "DocsURL": "",
        "MarketplaceURL": "",
        "SupportURL": "",
        "EnabledByDefault": True,
        "CanContainContent": True,
    }


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent="\t")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--files", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--depends", nargs="*", default=[])
    ap.add_argument("--steam-app-id", default="null")
    args = ap.parse_args()
    name = args.name
    steam_app_id = None if args.steam_app_id == "null" else int(args.steam_app_id)

    with tempfile.TemporaryDirectory() as root:
        shutil.copytree(args.files, root, dirs_exist_ok=True)
        plugins = os.path.join(root, "TS2Prototype", "Plugins")
        write_json(os.path.join(plugins, "DLC", name, f"{name}.uplugin"), descriptor(name))
        write_json(os.path.join(plugins, f"{name}.upluginmanifest"), {"Contents": [{
            "File": f"../../../TS2Prototype/Plugins/DLC/{name}/{name}.uplugin",
            "Descriptor": descriptor(name),
        }]})
        write_json(os.path.join(plugins, "DLC", name, "Content", f"{name}.dlc"), {
            "ContentPath": f"Plugins/DLC/{name}/Content/",
            "DLCDependencies": args.depends or None,
            "EpicStoreItemId": None,
            "Files": [],
            "GDKStoreId": None,
            "LiveProductId": None,
            "MountPoint": f"/{name}/",
            "Name": name,
            "PS5ContentId": None,
            "PSNContentId": None,
            "SteamAppId": steam_app_id,
            "UWPProductId": None,
        })
        build(root, args.out)


if __name__ == "__main__":
    main()
