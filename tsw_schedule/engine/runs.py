"""Group a route timetable's services into runs: what one real train does on the route under one headcode.

A unit's day is a chain of services linked by formation names (a service's EndOfServiceFormationName is the next
service's FormationName). Leaving the route through a portal parks the unit in the "vault" until a later service
brings it back. A run is a stretch of a chain with one headcode, from appearing at a portal (or starting at a
location on the route) to leaving through a portal (or ending at a location). Real trains are matched to runs.

    python -m tsw_schedule.engine.runs <timetable.uasset> [headcode ...]
"""
import os
import re
import sys
from dataclasses import dataclass, field

from .package import EditablePackage
from .props import decode_export

HEADCODE = re.compile(r"^([0-9][A-Z][0-9]{2})")


def enum_value(v):
    return str(v).split("::")[-1]


def headcode(service_name):
    m = HEADCODE.match(service_name)
    return m.group(1) if m else service_name


def is_portal(marker):
    return marker is not None and marker.startswith("Portal")


@dataclass
class Call:
    marker: str
    stopping: bool
    arrive_s: float     # simulated arrival (seconds after midnight)
    depart_s: float     # simulated completion of the stop (== arrive_s when passing)


@dataclass
class Run:
    headcode: str
    services: list                      # decoded service Structs, in running order
    chain: int                          # index of the unit's chain
    layer: str
    start_kind: str                     # "portal" (out of the vault) or "location"
    start_marker: str
    end_kind: str                       # "portal" (into the vault) or "location"
    end_marker: str
    calls: list = field(default_factory=list)
    start_s: float = 0.0
    end_s: float = 0.0
    player_drivable: bool = False
    train_entry: str = None             # TrainEntry of the unit (from the chain's initial formation)
    complex: str = None                 # why this run can't be used as a template (couple/uncouple), if so

    @property
    def names(self):
        return [str(s["Name"]) for s in self.services]

    @property
    def stops(self):
        return [c.marker for c in self.calls if c.stopping]

    def signature(self):
        """What a real train must match: where it comes from, every marker on the way (stop or pass), where it goes."""
        return (self.start_marker, tuple((c.marker, c.stopping) for c in self.calls), self.end_marker)

    def describe(self):
        def t(s):
            s = int(round(s))
            return f"{s // 3600:02d}:{s % 3600 // 60:02d}"
        stops = " ".join(c.marker.replace(" Platform ", "/") for c in self.calls if c.stopping)
        flags = (" drivable" if self.player_drivable else "") + (f" [complex: {self.complex}]" if self.complex else "")
        return (f"{self.headcode} {t(self.start_s)}-{t(self.end_s)} {self.layer}: {self.start_kind}:{self.start_marker}"
                f" -> {self.end_kind}:{self.end_marker} | stops: {stops} | {'+'.join(self.names)}{flags}")


class Timetable:
    """The decoded timetable plus lookups: services, formations, runs."""

    def __init__(self, uasset_path):
        self.path = uasset_path
        self.package = EditablePackage.load(uasset_path)
        self.export = decode_export(self.package, self.package.export_data(1))
        self.props = self.export.props
        self.services = self.props["Services"].items
        self.formations = {str(f["Name"]): f for f in self.props["Formations"].items}
        self.runs = build_runs(self)


def _instruction_calls(service):
    """Markers visited by a service, in order, with stop flag and simulated times."""
    calls, pending = [], None
    for ins in service["Instructions"].items:
        kind = enum_value(ins["InstructionType"])
        if kind == "GoTo":
            marker = str(ins["Destination"]["Name"])
            arrive = ins["SimulatedArrivalTime"].seconds
            pending = Call(marker, bool(ins.get("bIsStopping", False)) and not is_portal(marker), arrive, arrive)
            calls.append(pending)
        elif kind == "LoadUnload":
            depart = ins["SimulatedCompletionTime"].seconds
            if pending is not None:
                pending.depart_s = depart
                pending.stopping = True
            else:  # a stop where the service starts, before any GoTo
                calls.append(Call(None, True, ins["SimulatedArrivalTime"].seconds, depart))
    return calls


def _ribbon_key(location):
    """(ribbon guid bytes, position along it) of a NetworkRibbonLocation struct."""
    ref = location.get("RibbonReference")
    return (ref.raw if ref is not None else None), float(location.get("RibbonLocation", 0.0))


def marker_index(tt):
    """Every marker's track positions, so a spawn location can be named after the marker it is at."""
    index = {}
    for s in tt.services:
        for ins in s["Instructions"].items:
            dest = ins.get("Destination")
            if dest is None or dest.get("Location") is None:
                continue
            ribbon, pos = _ribbon_key(dest["Location"])
            index.setdefault(ribbon, []).append((pos, str(dest["Name"])))
    return index


def nearest_marker(index, location):
    ribbon, pos = _ribbon_key(location)
    candidates = index.get(ribbon)
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(c[0] - pos))[1]


def build_runs(tt):
    by_formation = {str(s["FormationName"]): s for s in tt.services}
    produced = {str(s["EndOfServiceFormationName"]) for s in tt.services}
    heads = [s for s in tt.services if str(s["FormationName"]) not in produced]
    markers = marker_index(tt)
    runs, seen = [], set()

    for chain_index, head in enumerate(heads):
        formation = tt.formations.get(str(head["FormationName"]))
        train_entry, spawn_marker, in_vault = None, None, False
        if formation is not None:
            entry = formation.get("TrainEntry")
            if entry is not None:
                train_entry = str(getattr(entry, "path", entry))
            in_vault = bool(formation.get("bSpawnInVault", False))
            if not in_vault and formation.get("SpawnLocation") is not None:
                spawn_marker = nearest_marker(markers, formation["SpawnLocation"])
        chain, s = [], head
        while s is not None and id(s) not in seen:
            seen.add(id(s))
            chain.append(s)
            s = by_formation.get(str(s["EndOfServiceFormationName"]))
        runs.extend(_split_chain(tt, chain, chain_index, train_entry, in_vault, spawn_marker))

    missing = [s for s in tt.services if id(s) not in seen]
    for s in missing:  # services fed by couple/uncouple products: keep them visible but unusable
        runs.extend(_split_chain(tt, [s], -1, None, False, None))
        runs[-1].complex = runs[-1].complex or "not reachable from a starting formation"
    return runs


def _split_chain(tt, chain, chain_index, train_entry, in_vault, spawn_marker):
    runs, current, last_marker, last_left_route = [], None, spawn_marker, in_vault
    for s in chain:
        calls = _instruction_calls(s)
        hc = headcode(str(s["Name"]))
        first = calls[0].marker if calls else None
        new_run = current is None or hc != current.headcode or last_left_route
        if new_run:
            start_kind = "portal" if is_portal(first) else "location"
            start_marker = first if start_kind == "portal" else (last_marker or first)
            current = Run(hc, [], chain_index, str(s.get("LayerName", "None")), start_kind, start_marker,
                          "location", None, train_entry=train_entry, start_s=s["StartTime"].seconds)
            runs.append(current)
        current.services.append(s)
        for c in calls:
            if c.marker is None:
                c.marker = last_marker or current.start_marker
            previous = current.calls[-1].marker if current.calls else current.start_marker
            if c.marker == previous:
                if current.calls and c.stopping:  # more instructions at the same place: one longer stop
                    current.calls[-1].stopping = True
                    current.calls[-1].depart_s = max(current.calls[-1].depart_s, c.depart_s)
                continue
            current.calls.append(c)
            if not is_portal(c.marker):
                last_marker = c.marker
        current.player_drivable = current.player_drivable or bool(s.get("bIsPlayerDrivable", False))
        kinds = {enum_value(i["InstructionType"]) for i in s["Instructions"].items}
        if kinds & {"Couple", "Uncouple"}:
            current.complex = "couples or uncouples"
        last_left_route = bool(calls) and is_portal(calls[-1].marker)
        if calls:
            current.end_s = calls[-1].depart_s
            current.end_kind = "portal" if last_left_route else "location"
            current.end_marker = calls[-1].marker
            while last_left_route and current.calls and is_portal(current.calls[-1].marker):
                current.calls.pop()
    return runs


def station_of(marker):
    """'Crewe Platform 7' -> 'Crewe', 'Stafford 5' -> 'Stafford'; portals and other markers unchanged."""
    if marker is None or is_portal(marker):
        return marker
    m = re.match(r"^(.*?)(?: Platform)? ?([0-9]+[a-zA-Z]?)$", marker)
    return m.group(1) if m else marker


def path_key(run):
    return (run.start_marker if run.start_kind == "portal" else station_of(run.start_marker),
            tuple(station_of(c.marker) for c in run.calls if c.stopping),
            run.end_marker if run.end_kind == "portal" else station_of(run.end_marker))


def path_catalog(runs):
    """Distinct journey shapes per layer: [(layer, path, runs)] sorted by layer then frequency."""
    groups = {}
    for r in runs:
        if not r.complex:
            groups.setdefault((r.layer, path_key(r)), []).append(r)
    return sorted(((layer, path, rs) for (layer, path), rs in groups.items()), key=lambda g: (g[0], -len(g[2])))


def main():
    tt = Timetable(sys.argv[1])
    args = sys.argv[2:]
    runs = tt.runs
    print(f"{len(tt.services)} services -> {len(runs)} runs; "
          f"{sum(1 for r in runs if r.complex)} complex; "
          f"{sum(1 for r in runs if r.start_kind == 'portal')} start at a portal, "
          f"{sum(1 for r in runs if r.end_kind == 'portal')} end at a portal")
    if args[:1] == ["--paths"]:
        def t(s):
            s = int(round(s))
            return f"{s // 3600:02d}:{s % 3600 // 60:02d}"
        for layer, (start, stops, end), rs in path_catalog(runs):
            hcs = sorted({r.headcode for r in rs})
            print(f"{layer:20s} {len(rs):3d}x {t(min(r.start_s for r in rs))}-{t(max(r.start_s for r in rs))} "
                  f"{start} > {' > '.join(stops) or '-'} > {end}  [{', '.join(hcs[:4])}{'...' if len(hcs) > 4 else ''}]")
        return
    wanted = set(args)
    for r in runs:
        if not wanted or r.headcode in wanted:
            print(" ", r.describe())


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    main()
