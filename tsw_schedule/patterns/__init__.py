"""Typical-day patterns: a route's standard weekday written as service patterns, turned into planned trains.

A pattern file (JSON) has a stock table (tokens like "390" -> TrainEntry paths, best first) and a list of services:

    {"id": "awc-liverpool-down", "operator": "Avanti West Coast", "name": "London Euston to Liverpool Lime Street",
     "path": {"from": "Portal - Bletchley Down Fast", "stops": ["Milton Keynes", "Crewe"],
              "to": "Portal - Winsford Down Fast"},
     "stock": ["807", "390"], "headcode": "1F", "first": 10,
     "times": {"from": "06:14", "to": "22:14", "every": 60}, "at": "entry", "drivable": true}

`path` must match a journey the route's own timetable makes (runs.path_key): entry portal or starting station,
stopping stations in order, exit portal or final station. `times` are when the train enters the route (at
"entry": appears at its portal or leaves its first station) or when it leaves/passes the station named in `at`.
`same_headcodes_as` reuses another pattern's headcodes in order (one train in two parts on the route).
"""
import json
import os

from ..engine.generate import PlannedTrain
from ..engine.runs import path_catalog, station_of

PATTERN_DIR = os.path.dirname(os.path.abspath(__file__))


def load(name):
    path = name if name.endswith(".json") else os.path.join(PATTERN_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_time(text):
    h, m = text.split(":")
    return int(h) * 3600 + int(m) * 60


def fmt_time(seconds):
    s = int(round(seconds)) % (48 * 3600)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


def times_of(spec):
    t = spec["times"]
    if isinstance(t, list):
        out = [parse_time(x) for x in t]
    else:
        out = list(range(parse_time(t["from"]), parse_time(t["to"]) + 1, int(t["every"]) * 60))
        out += [parse_time(x) for x in t.get("also", [])]
        skip = {parse_time(x) for x in t.get("except", [])}
        out = [x for x in out if x not in skip]
    return sorted(out)


def reference_time(run, at):
    if at in (None, "entry"):
        return run.start_s
    for c in run.calls:
        if station_of(c.marker) == at or c.marker == at:
            return c.depart_s if c.stopping else c.arrive_s
    return None


def resolve_stock(tokens, table, generator, owned):
    """First TrainEntry, in token order, that the route timetable can build and whose vehicles are installed.
    Table entries may be full TrainEntry paths or just their object names (e.g. "TV-390-1")."""
    by_name = {path.rsplit(".", 1)[-1]: path for path in generator.donors}
    for i, token in enumerate(tokens):
        for entry in table.get(token, [token]):
            entry = entry if entry in generator.donors else by_name.get(entry)
            if entry is None:
                continue
            if generator.stock_plugins(entry) <= set(owned):
                return token, entry, (i > 0)
    return None, None, True


class Planner:
    def __init__(self, pattern, timetable, generator, owned_plugins):
        self.pattern = pattern
        self.tt = timetable
        self.gen = generator
        self.owned = owned_plugins
        self.paths = {}
        for _layer, path, runs in path_catalog(timetable.runs):
            self.paths.setdefault(path, []).extend(runs)

    def path_of(self, spec):
        p = spec["path"]
        return (p["from"], tuple(p.get("stops", [])), p["to"])

    def plan(self):
        """Returns (plans, problems): planned trains and [(pattern id, message)] for what couldn't be planned."""
        plans, problems, headcodes = [], [], {}
        for spec in self.pattern["services"]:
            sid = spec["id"]
            runs = self.paths.get(self.path_of(spec))
            if not runs:
                problems.append((sid, f"no journey on this route matches {self.path_of(spec)}"))
                continue
            token, stock, substituted = resolve_stock(spec["stock"], self.pattern.get("stock", {}), self.gen,
                                                      self.owned)
            if stock is None:
                problems.append((sid, f"none of the stock {spec['stock']} is installed"))
                continue
            at = spec.get("at", "entry")
            numbers = headcodes.get(spec.get("same_headcodes_as"))
            mine = []
            for i, t in enumerate(times_of(spec)):
                # the template run of this shape closest in time of day (similar running times and dwells)
                candidates = [(r, reference_time(r, at)) for r in runs]
                candidates = [(r, ref) for r, ref in candidates if ref is not None]
                if not candidates:
                    problems.append((sid, f"journey doesn't call at {at}"))
                    break
                run, ref = min(candidates, key=lambda c: abs(c[1] - t))
                if numbers is not None and i < len(numbers):
                    hc = numbers[i]
                else:
                    hc = f"{spec['headcode']}{(spec.get('first', 1) + i * spec.get('step', 1)) % 100:02d}"
                mine.append(hc)
                plan = PlannedTrain(hc, run, t - ref, stock, spec.get("name"), spec.get("drivable"), pattern=sid)
                plan.operator = spec.get("operator", "")
                plan.stock_token = token
                if substituted:
                    plan.notes.append(f"{spec['stock'][0]} not installed: runs as {token}")
                plans.append(plan)
            headcodes[sid] = mine
        return plans, problems


def headway_warnings(plans, min_gap_s=180):
    """Trains appearing at the same portal less than min_gap_s apart (the second would wait or bunch)."""
    by_portal = {}
    for p in plans:
        if not p.dropped and p.template.start_kind == "portal":
            by_portal.setdefault(p.template.start_marker, []).append(p)
    out = []
    for portal, ps in sorted(by_portal.items()):
        ps.sort(key=lambda p: p.start_s)
        for a, b in zip(ps, ps[1:]):
            if b.start_s - a.start_s < min_gap_s:
                out.append(f"{portal}: {a.headcode} {fmt_time(a.start_s)} ({a.pattern}) and {b.headcode} "
                           f"{fmt_time(b.start_s)} ({b.pattern})")
    return out


def summary_rows(plans):
    """One row per planned train, for previews."""
    rows = []
    for p in sorted(plans, key=lambda p: p.start_s):
        run = p.template
        stops = [station_of(c.marker) for c in run.calls if c.stopping]
        rows.append({
            "headcode": p.headcode, "operator": getattr(p, "operator", ""), "pattern": p.pattern,
            "name": p.friendly_name or "", "start": fmt_time(p.start_s), "end": fmt_time(p.end_s),
            "from": run.start_marker, "to": run.end_marker, "stops": stops,
            "stock": getattr(p, "stock_token", ""), "drivable": bool(p.drivable),
            "status": "dropped" if p.dropped else ("turnround" if p.follows else "ok"),
            "notes": ([p.dropped] if p.dropped else []) + list(p.notes),
            "follows": p.follows.headcode if p.follows else None,
        })
    return rows
