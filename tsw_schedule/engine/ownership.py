"""Which content the player has: the plugins inside the game's installed paks (cached, keyed by pak sizes)."""
import glob
import json
import os

from .pakread import Pak


def installed_plugins(game_content, cache_file):
    """{plugin name: pak file name} for every .uplugin in Content/DLC/*.pak and Content/Paks/*.pak."""
    paks = sorted(glob.glob(os.path.join(game_content, "DLC", "*.pak")) +
                  glob.glob(os.path.join(game_content, "Paks", "*.pak")))
    key = {os.path.basename(p): os.path.getsize(p) for p in paks}
    if os.path.exists(cache_file):
        with open(cache_file, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("paks") == key:
            return cached["plugins"]
    plugins = {}
    for p in paks:
        try:
            pak = Pak(p)
        except (OSError, ValueError):
            continue
        for name in pak.files:
            if name.endswith(".uplugin"):
                plugins[name.rsplit("/", 1)[1][:-len(".uplugin")]] = os.path.basename(p)
    os.makedirs(os.path.dirname(os.path.abspath(cache_file)), exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"paks": key, "plugins": plugins}, f, indent=1)
    return plugins
