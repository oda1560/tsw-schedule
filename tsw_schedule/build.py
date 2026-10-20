"""Build a route's typical day from its pattern: plan, generate, pack as a mod, install.

    python -m tsw_schedule.build crewe-mk            # preview: plan only, print the trains
    python -m tsw_schedule.build crewe-mk --build    # generate the mod into build/
    python -m tsw_schedule.build crewe-mk --install  # ... and copy it to the game's UserContent folder
"""
import argparse
import os
import sys
import time

from .engine.generate import Generator, write_timetable
from .engine.modpack import ModNames, build_mod, install, rel_path
from .engine.ownership import installed_plugins
from .engine.pakread import Pak
from .engine.runs import Timetable
from .engine.tracks import LayerTrack
from .patterns import Planner, headway_warnings, load as load_pattern, summary_rows
from .routes import GAME, ROUTES, USER_CONTENT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRACTED = os.path.join(ROOT, "extracted")
BUILD = os.path.join(ROOT, "build")


def ensure_extracted(route, rels, log):
    """Extract route files (each .uasset with its .uexp) from the installed game pak if not extracted yet."""
    missing = []
    for rel in rels:
        for f in ([rel, rel[:-len(".uasset")] + ".uexp"] if rel.endswith(".uasset") else [rel]):
            if not os.path.exists(os.path.join(EXTRACTED, f)):
                missing.append(f)
    if not missing:
        return
    log(f"extracting {len(missing)} files from {os.path.basename(route['pak'])}")
    pak = Pak(route["pak"])
    for f in missing:
        dest = os.path.join(EXTRACTED, f)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as out:
            out.write(pak.extract(f))


class RouteSession:
    """A route's timetable, loaded once (the app keeps one per route)."""

    def __init__(self, route_id, log=print):
        self.route_id = route_id
        self.route = ROUTES[route_id]
        self.log = log
        ensure_extracted(self.route, [self.route["timetable"], self.route["master_track"],
                                      self.route["manifest_template"], self.route["registry_template"]], log)
        t0 = time.time()
        self.timetable_path = os.path.join(EXTRACTED, self.route["timetable"])
        self.tt = Timetable(self.timetable_path)
        log(f"loaded {self.route['name']} timetable: {len(self.tt.services)} services, {len(self.tt.runs)} runs "
            f"({time.time() - t0:.0f}s)")
        self.owned = installed_plugins(os.path.dirname(GAME), os.path.join(BUILD, "installed_plugins.json"))
        self.track_cache = {}

    def plan(self, pattern):
        """Plans for a pattern (fresh copies each call), with where each train's unit comes from."""
        generator = Generator(self.tt, {})
        plans, problems = Planner(pattern, self.tt, generator, self.owned).plan()
        generator.place_units(plans)
        return plans, problems

    def load_tracks(self, layers):
        """{layer: LayerTrack} for the given layers (recordings stay raw bytes; loaded once per session)."""
        out = {}
        for L in self.tt.props["Layers"].items:
            layer = str(L["Name"])
            if layer not in layers or L.get("DataTrack") is None or str(L["DataTrack"].path) == "None":
                continue
            if layer not in self.track_cache:
                rel = rel_path(str(L["DataTrack"].path).split(".")[0])
                ensure_extracted(self.route, [rel], self.log)
                t0 = time.time()
                self.track_cache[layer] = LayerTrack(os.path.join(EXTRACTED, rel))
                self.log(f"indexed recorded runs for {layer} ({time.time() - t0:.0f}s)")
            out[layer] = self.track_cache[layer]
        return out

    def build(self, pattern, mod_name, title, description, do_install=False):
        t0 = time.time()
        plans, problems = self.plan(pattern)
        layers = {p.template.layer for p in plans if not p.dropped}
        tracks = self.load_tracks(layers)
        # The timetable is edited in place while generating, so work on a fresh copy of it.
        tt = Timetable(self.timetable_path)
        generator = Generator(tt, tracks)
        for p in plans:  # re-point plans at the fresh copy's runs (same order)
            p.template = tt.runs[self.tt.runs.index(p.template)]
            p.follows, p.spawn_at, p.dropped, p.last_side = None, None, None, None
            p.notes = [n for n in p.notes if "not installed" in n]
        names = ModNames(mod_name, f"{os.path.basename(self.timetable_path)[:-len('.uasset')]}_{mod_name}")
        gen_dir = os.path.join(BUILD, "gen", mod_name)
        os.makedirs(gen_dir, exist_ok=True)
        _, tt_file, track_files = write_timetable(
            tt, plans, generator, os.path.join(EXTRACTED, self.route["master_track"]), gen_dir, names.track_ref,
            mod_name, title, description)
        self.log(f"generated {sum(1 for p in plans if not p.dropped)} trains ({time.time() - t0:.0f}s)")
        pak = build_mod(extracted=EXTRACTED, route=self.route, names=names, timetable_file=tt_file,
                        tracks=track_files, title=title, description=description,
                        out_pak=os.path.join(BUILD, f"{mod_name}.pak"))
        installed = install(pak, USER_CONTENT) if do_install else None
        self.log(f"built {os.path.basename(pak)} ({os.path.getsize(pak) / 1e6:.0f} MB) in {time.time() - t0:.0f}s"
                 + (f", installed to {installed}" if installed else ""))
        return plans, problems, pak, installed


def mod_name_for(pattern_id):
    """crewe-mk-typical -> TSWS_CreweMkTypical (the mod, its pak file and its plugin share this name)."""
    return "TSWS_" + "".join(part.capitalize() for part in pattern_id.replace("_", "-").split("-"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("route", choices=sorted(ROUTES))
    ap.add_argument("--pattern", default=None, help="pattern file name (default: <route>-typical)")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--all", action="store_true", help="list every planned train, not just those with notes")
    args = ap.parse_args()
    pattern_id = args.pattern or f"{args.route}-typical"
    pattern = load_pattern(pattern_id)
    session = RouteSession(args.route)
    if args.build or args.install:
        plans, problems, _, _ = session.build(pattern, mod_name_for(pattern_id), pattern["title"],
                                              pattern["description"], do_install=args.install)
    else:
        plans, problems = session.plan(pattern)
    rows = summary_rows(plans)
    ok = sum(1 for r in rows if r["status"] != "dropped")
    close = headway_warnings(plans)
    print(f"\n{ok} trains planned, {len(rows) - ok} dropped, {len(problems)} pattern problems, "
          f"{len(close)} close entries")
    for pid, msg in problems:
        print(f"  PROBLEM {pid}: {msg}")
    for w in close:
        print(f"  CLOSE {w}")
    for r in rows:
        if args.all or r["status"] == "dropped" or any("not installed" not in n for n in r["notes"]):
            print(f"  {r['headcode']} {r['start']} {r['pattern']}: {r['status']} {'; '.join(r['notes'])}")


if __name__ == "__main__":
    sys.exit(main())
