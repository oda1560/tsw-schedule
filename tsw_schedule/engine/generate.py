"""Generate a new day for a route: clone template runs from the route's timetable at new times, with chosen stock.

Each PlannedTrain names a template Run (from runs.py), its new headcode, a time shift and a stock (a TrainEntry
that some Initial formation in the route timetable already uses, so its vehicle list is known to work). The
generator writes:
  - services: the template run's services, renamed, re-timed, without layers;
  - formations: the end formation of an earlier train whose unit it takes over (turning round on the route, or
    back out of the vault), else a fresh Initial formation (in the vault when it enters from a portal, otherwise
    where the template's unit started the day); and one Generated end formation per service. Every train gets new vehicle ids, registered in CompiledRVMap;
  - one data track holding every service's recorded run (copied from the template's layer track, re-timed).
"""
import collections
import copy
import struct as st
import uuid
from dataclasses import dataclass, field

from .props import (Array, Encoder, FName, MapValue, Native, Prop, Raw, SoftObject, Str, Struct, Text,
                    decode_export, encode_export, names_used, timespan_value_offsets)
from .package import EditablePackage, fstring_bytes
from .runs import is_portal

CLOCK_FIELDS = {"StartTime", "SimulatedStartTime", "ArrivalTime", "SimulatedArrivalTime", "CompletionTime",
                "SimulatedCompletionTime", "Time"}
TICKS = 10_000_000
NONE = FName("None")


@dataclass
class PlannedTrain:
    headcode: str
    template: object            # runs.Run
    shift_s: float              # added to every template time
    stock: str                  # TrainEntry path
    friendly_name: str = None   # menu name; default: template's with the headcode replaced
    drivable: bool = None       # default: as the template
    pattern: str = ""           # which pattern produced it (for reports)
    follows: "PlannedTrain" = None
    spawn_at: str = None        # start-of-day position (marker) when the unit starts the day on the route
    dropped: str = None         # why it couldn't be placed, if so
    last_side: str = None       # leading end of the unit at the end of this train (for a turnaround after it)
    via_vault: bool = False     # its unit is an earlier train's, back out of the vault
    vault_flip: bool = False    # ... and leads with the other end than the one it left with
    # filled by the generator
    service_names: list = field(default_factory=list)
    end_formation: str = None
    vehicles: list = None       # RailVehicleInfo structs (with this train's vehicle ids)
    notes: list = field(default_factory=list)

    @property
    def start_s(self):
        return self.template.start_s + self.shift_s

    @property
    def end_s(self):
        return self.template.end_s + self.shift_s


def shift_times(node, delta_ticks):
    """Add delta to every set clock time (StartTime, ArrivalTime, ..., data-track Time) under node."""
    if isinstance(node, Struct):
        for p in node.props:
            if p.name.text in CLOCK_FIELDS and isinstance(p.value, Native) and p.value.struct_name == "Timespan":
                if p.value.ticks > 0:
                    p.value.ticks += delta_ticks
            else:
                shift_times(p.value, delta_ticks)
    elif isinstance(node, Array):
        for item in node.items:
            shift_times(item, delta_ticks)
    elif isinstance(node, MapValue):
        for _, v in node.items:
            shift_times(v, delta_ticks)


def set_prop(struct, name, value, type_name):
    p = struct.prop(name)
    if p is None:
        p = Prop(FName(name), FName(type_name))
        struct.props.append(p)
    if type_name == "BoolProperty":
        p.bool_value = bool(value)
    else:
        p.value = value
    return p


def text(key, source):
    """FText with Base history and a key no localisation table has, so the source string shows."""
    import struct as st
    return Text(st.pack("<Ib", 0, 0) + fstring_bytes("") + fstring_bytes(key) + fstring_bytes(source))


def text_source(t):
    """The source string of a Base-history FText (for building new menu names from old ones)."""
    import struct as st
    raw = t.raw
    if len(raw) < 5 or st.unpack_from("<b", raw, 4)[0] != 0:
        return ""
    pos = 5
    out = ""
    for _ in range(3):
        n = st.unpack_from("<i", raw, pos)[0]
        pos += 4
        if n >= 0:
            out = raw[pos:pos + n][:-1].decode("latin-1") if n else ""
            pos += n
        else:
            out = raw[pos:pos - 2 * n][:-2].decode("utf-16-le")
            pos -= 2 * n
    return out


FRONT, BACK = "ESide::Front", "ESide::Back"


def leading_side(service):
    """Which end of the unit's vehicle list leads (and is driven from) in this service. A unit's vehicle list
    never changes through its day; services say which end leads, so it flips each time the train reverses."""
    return str(service.get("PlayerDrivableSide", FRONT))


def other_side(side):
    return BACK if side == FRONT else FRONT


def set_enum(struct, name, enum_type, value):
    p = struct.prop(name)
    if p is None:
        p = Prop(FName(name), FName("EnumProperty"), enum_name=FName(enum_type))
        struct.props.append(p)
    p.value = FName(value)


def suffix_of(service_name, headcode):
    """'1U00_AI_2' -> '_AI_2'; descriptive tails ('5W10 0616 Crewe CS to Crewe') are dropped."""
    rest = service_name[len(headcode):]
    if rest.startswith("_") or rest.startswith("-") or rest == "":
        return rest
    return ""


class Generator:
    def __init__(self, timetable, datatracks):
        """timetable: runs.Timetable; datatracks: {layer name: tracks.LayerTrack} for the layers used."""
        self.tt = timetable
        self.layer_tracks = datatracks
        self.track_layer = {}  # template service name -> layer whose data track records it
        for layer, lt in datatracks.items():
            for name in lt.entries:
                self.track_layer[name] = layer
        self.rv_map = {item[0].raw: item[1] for item in timetable.props["CompiledRVMap"].items}
        self.donors = {}
        for f in timetable.formations.values():
            if str(f["FormationType"]).endswith("Initial") and f.get("TrainEntry") is not None:
                self.donors.setdefault(str(f["TrainEntry"].path), f)
        self.spawn_points = {}  # start marker -> Initial formation of a unit that starts the day there
        self.spawn_sides = {}   # start marker -> leading side of the route's own first train from there
        for run in timetable.runs:
            head = timetable.formations.get(str(run.services[0]["FormationName"]))
            if (run.start_kind == "location" and head is not None and str(head["FormationType"]).endswith("Initial")
                    and not head.get("bSpawnInVault", False) and run.start_marker not in self.spawn_points):
                self.spawn_points[run.start_marker] = head
                self.spawn_sides[run.start_marker] = leading_side(run.services[0])
        # How the route's own day brings units back out of the vault: for each (exit portal, entry portal), per
        # stock and over all stock, how often the unit came back leading with the same end or the other one, and
        # how long it stayed away.
        self.vault_ends = collections.Counter()
        self.vault_waits = collections.defaultdict(list)
        chains = collections.defaultdict(list)
        for run in timetable.runs:
            if run.chain >= 0:
                chains[run.chain].append(run)
        for runs in chains.values():
            runs.sort(key=lambda r: r.start_s)
            for a, b in zip(runs, runs[1:]):
                if a.end_kind == "portal" and b.start_kind == "portal":
                    flip = leading_side(a.services[-1]) != leading_side(b.services[0])
                    pair = (a.end_marker, b.start_marker)
                    self.vault_ends[pair + (b.train_entry, flip)] += 1
                    self.vault_ends[pair + (flip,)] += 1
                    if b.start_s > a.end_s:
                        self.vault_waits[pair].append(b.start_s - a.end_s)

    # -- stock ---------------------------------------------------------------------------------------------------
    def stock_plugins(self, train_entry):
        """Plugins whose vehicles a donor formation uses (to check the player has them)."""
        donor = self.donors[train_entry]
        plugins = set()
        for v in donor["RailVehicleInfo"].items:
            rvd = self.rv_map.get(v["RailVehicleID"].raw)
            if rvd is not None:
                plugins.add(str(rvd.path).split("/")[1])
        return plugins

    def new_vehicles(self, train_entry, rv_entries):
        vehicles = copy.deepcopy(self.donors[train_entry]["RailVehicleInfo"].items)
        for v in vehicles:
            old = v["RailVehicleID"].raw
            new = uuid.uuid4().bytes
            v.prop("RailVehicleID").value = Native("Guid", new)
            rv_entries.append((Native("Guid", new), copy.deepcopy(self.rv_map[old])))
        return vehicles

    def vault_flip(self, exit_marker, entry_marker, stock):
        """Whether a unit that left at exit_marker comes back in at entry_marker leading with the other end, as
        the route's own day does it (for that stock, else over all stock); None when the day doesn't say clearly."""
        pair = (exit_marker, entry_marker)
        for key, least in ((pair + (stock,), 3), (pair, 10)):
            kept, flipped = self.vault_ends[key + (False,)], self.vault_ends[key + (True,)]
            if kept + flipped >= least:
                if kept >= 0.9 * (kept + flipped):
                    return False
                if flipped >= 0.9 * (kept + flipped):
                    return True
                return None
        return None

    def vault_wait(self, exit_marker, entry_marker, least_s):
        """Shortest time away between those portals: the route's own shortest for the pair, at least least_s."""
        waits = self.vault_waits.get((exit_marker, entry_marker))
        return max(least_s, min(waits)) if waits else least_s

    # -- generation ----------------------------------------------------------------------------------------------
    def place_units(self, plans, min_turn_s=8 * 60, latest_spawn_s=9 * 3600, min_vault_s=20 * 60):
        """Where each train's unit comes from. Entering from a portal: the unit of an earlier train of the same
        stock that left the route long enough ago, back out of the vault (only between portals where the route's
        own day shows which end then leads), else a new unit in the vault. Starting on the route: the unit of an
        earlier train of the same stock that ended there (same platform first, then same station; enough time to
        turn round), else, for the morning's first departures, a start-of-day position known from the route's own
        timetable (each used once; a unit placed there waits until it leaves), else the train is dropped."""
        from .runs import station_of
        free = []         # trains ending on the route whose unit hasn't been reused yet
        vault = []        # trains that left the route: their units wait in the vault for another train
        spawned = set()   # start-of-day positions already taken
        for p in sorted(plans, key=lambda p: p.start_s):
            run = p.template
            if run.start_kind == "portal" and p.follows is None:
                # Like the route's own day, units come back out of the vault (the longest-waiting one), so the
                # day needs a few hundred units rather than one per train; the game can't load a unit per train
                # on a busy day.
                ready = [q for q in vault if q.stock == p.stock
                         and q.end_s + self.vault_wait(q.template.end_marker, run.start_marker, min_vault_s) <= p.start_s
                         and self.vault_flip(q.template.end_marker, run.start_marker, p.stock) is not None]
                pick = min(ready, key=lambda q: q.end_s, default=None)
                if pick is not None:
                    p.follows = pick
                    p.via_vault = True
                    p.vault_flip = self.vault_flip(pick.template.end_marker, run.start_marker, p.stock)
                    vault.remove(pick)
            if run.start_kind == "location" and p.follows is None:
                ready = [q for q in free if q.end_s + min_turn_s <= p.start_s and q.stock == p.stock]
                same_platform = [q for q in ready if q.template.end_marker == run.start_marker]
                same_station = [q for q in ready if station_of(q.template.end_marker) == station_of(run.start_marker)]
                pick = max(same_platform or same_station, key=lambda q: q.end_s, default=None)
                if pick is not None:
                    p.follows = pick
                    free.remove(pick)
                    if pick.template.end_marker != run.start_marker:
                        p.notes.append(f"turns round at {pick.template.end_marker} (pattern starts at "
                                       f"{run.start_marker})")
                    if pick.stock != p.stock:
                        p.notes.append(f"uses {pick.headcode}'s unit")
                        p.stock = pick.stock
                elif (run.start_marker in self.spawn_points and run.start_marker not in spawned
                      and p.start_s <= latest_spawn_s):
                    p.spawn_at = run.start_marker
                    spawned.add(run.start_marker)
                else:
                    p.dropped = f"no train available at {run.start_marker} (nothing arrives there before it)"
                    continue
            if run.end_kind == "location":
                free.append(p)
            else:
                vault.append(p)

    def generate(self, plans):
        """Returns (services, formations, CompiledRVMap items, data-track items, placed trains)."""
        self.place_units(plans)
        services, formations, rv_entries, track_items, report = [], [], [], [], []
        used_names = set()
        for p in sorted((p for p in plans if not p.dropped), key=lambda p: p.start_s):
            run = p.template
            delta = int(round(p.shift_s * TICKS))
            # names
            new_names = []
            for s in run.services:
                base = p.headcode + suffix_of(str(s["Name"]), run.headcode)
                name, n = base, 2
                while name in used_names:
                    name, n = f"{base}_{n}", n + 1
                used_names.add(name)
                new_names.append(name)
            p.service_names = new_names

            # the unit: turnaround, or a new formation
            if p.follows is not None and p.follows.end_formation is not None:
                start_formation = p.follows.end_formation
                p.vehicles = p.follows.vehicles
            else:
                p.vehicles = self.new_vehicles(p.stock, rv_entries)
                start_formation = f"{new_names[0]}_Start"
                formations.append(self.initial_formation(p, start_formation))

            # Which end of our unit leads. The template's sides suit its own unit's vehicle order; ours may be the
            # other way round, so flip them all when needed: a turnaround leaves with the end that arrived last;
            # a unit back from the vault keeps or swaps ends as the route's own day does between those portals;
            # a unit placed at the start of the day leads like the route's own first train from that spot; a new
            # one in the vault leads as the template says. Wrong sides put the driver in a cab in the middle.
            first_side = leading_side(run.services[0])
            if p.via_vault:
                wanted = other_side(p.follows.last_side) if p.vault_flip else p.follows.last_side
            elif p.follows is not None and p.follows.last_side is not None:
                wanted = other_side(p.follows.last_side)
            elif p.spawn_at is not None:
                wanted = self.spawn_sides.get(p.spawn_at, first_side)
            else:
                wanted = first_side
            flip = wanted != first_side
            p.flipped = flip

            previous_end = start_formation
            for i, (s, name) in enumerate(zip(run.services, new_names)):
                svc = copy.deepcopy(s)
                shift_times(svc, delta)
                set_prop(svc, "Name", FName(name), "NameProperty")
                side = leading_side(s)
                if flip:
                    side = other_side(side)
                    set_enum(svc, "PlayerDrivableSide", "ESide", side)
                    if svc.get("EOTDSide") is not None:
                        set_enum(svc, "EOTDSide", "ESide", other_side(str(svc["EOTDSide"])))
                p.last_side = side
                set_prop(svc, "FormationName", FName(previous_end), "NameProperty")
                end_name = f"{name}_End"
                set_prop(svc, "EndOfServiceFormationName", FName(end_name), "NameProperty")
                if svc.get("FriendlyName") is not None:
                    old = text_source(svc["FriendlyName"])
                    if p.friendly_name and svc.get("bIsPlayerDrivable", False):
                        menu = f"{p.headcode}: {p.friendly_name}"
                    else:
                        menu = old.replace(run.headcode, p.headcode, 1) if old else name
                    set_prop(svc, "FriendlyName", text(f"tsws_{name}", menu), "TextProperty")
                if svc.get("ServiceNumber") is not None:
                    set_prop(svc, "ServiceNumber", Str(p.headcode), "StrProperty")
                if p.drivable is not None:
                    set_prop(svc, "bIsPlayerDrivable", p.drivable and svc.get("bIsPlayerDrivable", False),
                             "BoolProperty")
                services.append(svc)
                formations.append(self.end_formation(s, name, end_name, p))
                layer = self.track_layer.get(str(s["Name"]))
                if layer is not None:
                    track_items.append((layer, name, str(s["Name"]), delta))
                else:
                    p.notes.append(f"no recorded run for {s['Name']}")
                previous_end = end_name
            p.end_formation = previous_end
            report.append(p)
        return services, formations, rv_entries, track_items, report

    def initial_formation(self, p, name):
        donor = self.donors[p.stock]
        f = copy.deepcopy(donor)
        run = p.template
        set_prop(f, "Name", FName(name), "NameProperty")
        set_prop(f, "LayerName", FName(run.layer), "NameProperty")
        f.prop("RailVehicleInfo").value.items = p.vehicles
        if run.start_kind == "portal":
            set_prop(f, "bSpawnInVault", True, "BoolProperty")
            vault = next((x for x in self.tt.formations.values() if str(x["FormationType"]).endswith("Initial")
                          and x.get("bSpawnInVault", False)), None)
            for field_name in ("SpawnLocation", "InitialFormationRange", "SpawnFacing"):
                if vault is not None and vault.prop(field_name) is not None:
                    f.props = [q for q in f.props if q.name.text != field_name] + [copy.deepcopy(vault.prop(field_name))]
        else:
            spawn = self.spawn_points[p.spawn_at]
            set_prop(f, "bSpawnInVault", False, "BoolProperty")
            for field_name in ("SpawnLocation", "InitialFormationRange", "SpawnFacing", "SpawnType"):
                if spawn.prop(field_name) is not None:
                    f.props = [q for q in f.props if q.name.text != field_name] + \
                              [copy.deepcopy(spawn.prop(field_name))]
            p.notes.append(f"starts the day at {p.spawn_at}")
        return f

    def end_formation(self, template_service, service_name, name, p):
        template = self.tt.formations[str(template_service["EndOfServiceFormationName"])]
        f = copy.deepcopy(template)
        set_prop(f, "Name", FName(name), "NameProperty")
        set_prop(f, "SpawnedByServiceName", FName(service_name), "NameProperty")
        f.prop("RailVehicleInfo").value.items = copy.deepcopy(p.vehicles)
        return f


def write_timetable(tt, plans, generator, master_track, out_dir, track_ref, title_key, title, description):
    """Write the generated timetable and its data tracks into out_dir, still under the route's package names
    (modpack moves them into the mod). track_ref(original track package path) -> the reference the timetable
    should use for it. Returns (placed trains, timetable file, [(original track package, file)])."""
    import os
    from .tracks import LayerTrack, write_track
    services, formations, rv_entries, track_items, report = generator.generate(plans)

    props = tt.props
    props.prop("Services").value.items = services
    props.prop("Formations").value.items = formations
    props.prop("CompiledRVMap").value.items = rv_entries
    if props.prop("Dependancies") is not None:
        props.prop("Dependancies").value.items = []
    set_prop(props, "bDefaultTimetable", False, "BoolProperty")
    set_prop(props, "TimetableName", text(f"{title_key}_Name", title), "TextProperty")
    set_prop(props, "TimetableDescription", text(f"{title_key}_Description", description), "TextProperty")

    # Layers: keep those the new services use, each pointing at our copy of its data track, needing no DLC
    # (the stock may have been swapped for something installed).
    written = []
    by_layer = {}
    for layer, name, template, delta in track_items:
        by_layer.setdefault(layer, []).append((name, template, delta))
    used = {str(s["LayerName"]) for s in services}
    kept = []
    for layer_struct in props["Layers"].items:
        layer = str(layer_struct["Name"])
        if layer not in used:
            continue
        kept.append(layer_struct)
        original = str(layer_struct["DataTrack"].path).split(".")[0]
        lt = generator.layer_tracks[layer]
        recordings = [(name, lt.recording(template, delta)) for name, template, delta in by_layer.get(layer, [])]
        out = os.path.join(out_dir, f"{original.rsplit('/', 1)[1]}.uasset")
        write_track(lt, recordings, out)
        written.append((original, out))
        layer_struct.prop("DataTrack").value = SoftObject(FName(track_ref(original)), Str(""))
        required = layer_struct.prop("RequiredDLC")
        if required is not None:
            required.value.items = []
    props.prop("Layers").value.items = kept

    # Master data track: an empty copy (its junction recording belongs to the original day).
    master = LayerTrack(master_track)
    original = str(props["MasterDataTrack"].path).split(".")[0]
    out = os.path.join(out_dir, f"{original.rsplit('/', 1)[1]}.uasset")
    write_track(master, [], out)
    written.append((original, out))
    set_prop(props, "MasterDataTrack", SoftObject(FName(track_ref(original)), Str("")), "SoftObjectProperty")

    pkg = tt.package
    pkg.add_names(names_used(tt.export))
    pkg.replace_export_data(1, encode_export(pkg, tt.export))
    tt_file = os.path.join(out_dir, os.path.basename(tt.path))
    pkg.save(tt_file)
    return report, tt_file, written
