# TSW7 timetable format (from Isle of Wight V2)

Found 2026-10-19 by reading the game's own files (TSW7, Unreal Engine 4.26.1) and UE4SS reflection.

## Where it lives
- Route paks: `WindowsNoEditor\TS2Prototype\Content\DLC\TS2Prototype-WindowsNoEditor-<Route>.pak`
  - pak v11, index not encrypted, Zlib compression. `tools/paklist.py` lists and extracts.
- Isle of Wight is three plugins in one pak: `IsleOfWight_V2` (world), `IOW_V2_Route_Gameplay` (timetable,
  scenarios), `IOW_IL_Class484` (train).
- Timetable: `IOW_V2_Route_Gameplay/Content/Timetable/IOWV2_Timetable_TT` (one `RouteTimetableDefinition`, 1.9 MB)
- Its recorded simulation: `.../Timetable/DataTracks/IOWV2_Timetable_TT_MasterDataTrack` (`RouteTimetableDataTrackStream`, 3.3 MB)
- Station list: `IsleOfWight_V2/Content/Scenery/Stations/IOW_StationDirectory` (`StationDirectory`)
- Each scenario has its own small timetable: `Scenarios/ScNN/IOWV2_ScNN_TT`

## Serialization
- Cooked with **tagged** properties (no `PKG_UnversionedProperties`), so every value carries its name and type:
  `tools/uasset_dump.py` turns an asset into JSON with no type mappings needed.
- Properties at their default value are not written; the class/struct definitions list every field.
- Times are `FTimespan` (ticks of 100 ns) = seconds since midnight. Units (`SpeedQuantity`, `DistanceQuantity`)
  are one float in SI units.
- An FName is (name-map index, number), and the engine always splits a trailing `_<digits>` (no leading zero) off
  into the number: "WMR_350" is ("WMR", 351), "5F30_1" is ("5F30", 2). Cooked data is always stored that way.
  A name written as the literal ("WMR_350", 0) is a different name to the engine, so it matches nothing, though it
  prints the same. Build names from text with `FName.parse`.

## RouteTimetableDefinition
- `Services[]` (`RouteTimetableServiceDefinition`), `Formations[]` (`RouteTimetableFormationDefinition`)
- `Route` -> the route definition; `Dispatcher` = `DispatcherAIV3`, `YardManager` = `YardManagerAIV2`
- `bHasValidDataTrack`, `MasterDataTrack`, `DataTrackExpectedVersionNumber` (3496 = the data track's
  `VersionNumber`), `SimulatedWithVersion`
- `FrontendStartingTime`, `SimulationTimeLimit` (24 h), `TimetableVersion` = Version6
- Also in the class but unused here: `Layers`, `LayerDataTracks`, `Dependancies`, `ServiceStartTimeOverwriteMode`,
  `bRequiresExplicitCompile`, `bRemoveOffPathTrains`

## A service (RouteTimetableServiceDefinition)
- `Name` is the headcode-based id (`2D01-A`), `FriendlyName` what the menu shows (`2D01-A: Ryde Pier Head - Shanklin`)
- `StartTime`, `MinDwellTime`, `FormationName` (the train it uses), `EndOfServiceFormationName` (what the train is
  called afterwards). Services chain through formations: `2U02` ends as `2U02_End`, which `2D01-A` starts with;
  that is the unit's diagram.
- `bIsPlayerDrivable`, `bIsHiddenInFrontend`, `ServiceDisplayFlags`, `MapPointA`/`MapPointB`, `Description`,
  `ScoreThresholds`
- `ServicePath` is empty: paths are found at runtime from the destinations, not stored.
- `Instructions[]`, one per step:
  - `GoTo`: `Destination.Name` = a platform marker (`Sandown Platform 2`) plus its track location
    (`RibbonReference` GUID + `RibbonLocation`), `GoToTolerance`, `bIsStopping`, early/late tolerance.
  - `LoadUnload`: the station stop that follows the GoTo; its `ArrivalTime` is set and `CompletionTime` is
    rounded up to the scheduled departure.
  - `SimulatedArrivalTime`/`SimulatedCompletionTime`: the editor's simulated run.

## Data track (RouteTimetableDataTrackStream)
- `ServiceDataTracks`: map of service name -> list of points (track location, time, distance, stop/signal
  events): where each train is at each moment in the simulated day.
- `JunctionDataTrack`: when each junction switches.
- The game uses it to place AI trains when you start partway through the day, so changing service times means
  updating it too (or shifting a copied service's track by the same offset).

## Stations (StationDirectory)
- `Stations[]`: `StationName`, display names, `PlatformsNew[]` = marker name (`Shanklin Platform 1`) + platform
  number. Marker names are what a generated timetable maps real stations/platforms onto.

## Crewe - Milton Keynes (WCMLTrentValleyCreweMiltonKeynes.pak)
- Timetables `AABS_TrentValley_Gameplay/Content/Timetable/WCMLTT` (full, 2410 services, 37.6 MB) and
  `WCMLTTReduced`. Services are split over 35 `Layers` (one per operator/stock, with `RequiredDLC`); each layer has
  its own data track `DataTracks/WCMLTT_<layer>_DataTrack`, which holds that layer's services' recorded runs.
- Player services chain into AI-only parts off the route: `1U00` (Crewe 05:08 -> Milton Keynes) continues as
  `1U00_AI` to `Portal - Bletchley Up Fast`; `1U25_AI` brings `1U25` onto the route.
- Clock times in a service: `StartTime`, `SimulatedStartTime`, and per instruction `ArrivalTime`, `CompletionTime`,
  `Simulated*`, plus `VirtualDestinations[].ArrivalTime/CompletionTime`. Durations: `MinDwellTime`,
  `ScheduledDwellTime`, `WaitingTime`, tolerances, `LoadingCriteria[].MinimumTime`.

## Patching (first test, 2026-10-19)
- `tools/shift_services.py` shifts whole services by N minutes in the timetable and their layer's data track, in
  place (8-byte times, so sizes don't change). `tools/pakbuild.py` packs the changed files as an uncompressed v11 pak
  (full directory index only) at the same paths.
- Test: 1U00 + 1U00_AI +7 min (Crewe 05:15, Milton Keynes 06:54). Not shifted: the data track's
  `JunctionDataTrack` (points changes the recorded run made), which has no service link.
- The menu still showed 05:08. What we learned about how TSW7 loads content:
  - Paks in `Content\Paks` other than the base pak are never mounted.
  - Mods go in `Documents\My Games\TrainSimWorld7\Saved\UserContent\` (needs Settings > Advanced > Enable Mods,
    `bAllowMods=True` in GameUserSettings.ini) or in the game's `Content\DLC\` folder.
  - The DLC manager (`DLCManagementSystem`) registers a pak only if the pak's file name equals the `Name` in its
    `.dlc` (`TimetableShiftTest_P.pak` with Name `TimetableShiftTest` was silently skipped; `TimetableShiftB.pak` with
    Name `TimetableShiftB` registered). Registered names: `CachedContentDLCNames`; rejects: `FailedPakFiles`.
  - `tools/make_mod.py` builds a mod pak: own plugin (.uplugin, .upluginmanifest, .dlc with DLCDependencies) plus
    the files it carries. SteamAppId null and the base game's id both register.
  - Timetables reach the menu through each plugin's `ContentManifest` (`UContentManifest.DefinedTimetables_PC`);
    for this route it is the `WCMLTrentValley` plugin's. The DLC manager loads them at startup (WCMLTT was loaded
    ~20 s after launch, original copy), while a registered mod pak without a manifest isn't even opened at the menu.
    So overriding a Dovetail timetable can't change the menu; a mod should ship its own timetable asset and its own
    ContentManifest listing it.
- Marker for "whose copy is loaded": WCMLTT's `bUseTSW4Scoring` (true in the original, set false in test mods);
  the probe logs it whenever the game has WCMLTT loaded.

## A mod with its own timetable (works: mounted and loaded at startup, 2026-10-19)
- `tools/build_timetable_mod.py` builds it: a renamed copy of the route timetable in the mod's plugin
  (`/TimetableToday/Timetable/WCMLTT_Today`), own name/description (FText with a key no locres has), not default,
  shifted services; data tracks of changed layers copied into the mod (others keep pointing at Dovetail's);
  a `ContentManifest` with `DefinedTimetables_PC` = the copy; and an `AssetRegistry.bin`.
- **The AssetRegistry.bin is what makes the game see it.** Without one, the mod registered (CachedContentDLCNames)
  but was never mounted and its manifest never read. Dovetail's route pak has one registry
  (`WCMLTrentValley/Content/AssetRegistry.bin`, version 7) covering every plugin in the pak; timetable entries
  carry tags the menu uses: Route, TimetableName/TimetableDescription (NSLOCTEXT), bDefaultTimetable,
  bAvailableInServiceMode, bUserConfigurableLayers, FrontendStartingTime, TargetPlatformFlags, VersionNumber,
  bIsLooping. `tools/assetregistry.py` reads/writes it (round-trips Dovetail's file byte for byte).
- `tools/uasset_edit.py` renames name-map entries (recomputing FName hashes: StrihashDeprecated / StrCrc32, both
  verified on 5,676 names) and resizes exports, fixing every summary offset and export SerialOffset.

## Typical-day generator (2026-10-20, loads in game)
- Pattern files (`tsw_schedule/patterns/<route>.json`): stock table + service patterns (operator, path = entry
  portal/station, stops, exit; stock preference; headcodes; times). `runs.py --paths` lists the paths a route
  supports (262 on Crewe - Milton Keynes).
- Planner: per train, the template run of that path nearest in time of day; stock = first preference whose
  vehicles' plugins are installed (`engine/ownership.py`, cached in build/installed_plugins.json).
- Units: portal entries take back the unit of an earlier train of the same stock that left at a portal (the
  longest-waiting one; at least 20 min away and no less than Dovetail's shortest gap for that portal pair), else
  a new Initial formation in the vault; trains starting on the route take over an earlier train's unit of the
  same stock that ended there (same platform, else same station; >= 8 min, or a pattern's `turn` minutes, e.g.
  0 for the single-platform Cardiff Bay shuttle), else a start-of-day position, else dropped.
- Start-of-day positions: every non-vault Initial formation of Dovetail's day (several per marker, "Crewe Platform
  3 #2"), each used once, departures before 09:00, only where Dovetail's unit had at least as many vehicles (so a
  390 is never put in a 4-car space). Many sit on siding/depot ribbons with no instruction marker; they're named
  after their first train's first stop. Early empty runs from depots (Oxley via Bilbrook, Canton) feed stations
  that have no fitting position.
- Layer `RequiredDLC` holds plugin names (e.g. `CCC_TFW_Class150`, `AABS_Class350_2`), and Dovetail uses it to
  bring in trains from other packs (Birmingham - Crewe's TFW layer lists the Cardiff 150/153; Crewe - MK's WMR-350
  layer lists the Birmingham pack's 350/2 and 350/3). Each kept layer lists the plugins of the stock our trains in
  it use (all installed, since the planner only picks owned stock).
- Our Birmingham - Crewe timetables offered only the 390, 170/5 and 66 in the menu's train list (no 350, TfW
  150/153, WMR 170 or XC 170/6). The cause was names: we wrote service names, formation names, `LayerName`,
  `SpawnedByServiceName`, `RequiredDLC` entries and data-track keys as literal FNames. Those that must match a
  name stored properly (a unit's `LayerName` against the layer list kept from Dovetail's data, `RequiredDLC`
  against plugin names) matched nothing, so every unit in a layer whose name ends in digits (WMR_350, LNR_350,
  WMT_170, XC_170, ...) was left out. Units in layers named without digits (AWC, XC_Voyager) were offered. Found by menu tests that moved
  Dovetail's own trains into our timetable one change at a time (2026-10-21). Packaging, layers, dependencies,
  RequiredDLC, GUIDs, times and texts were ruled out on the way.
- New vault units copy SpawnLocation / InitialFormationRange / SpawnFacing from the route's own vault formation
  for that entry (the template's own starting formation if it has one, else one whose train enters the same way,
  preferring an all-zero ribbon = no track position). Copying the first vault formation for every unit gave
  Birmingham - Crewe 141 units all positioned near Heath Town (its first vault formation has a real position) and
  the Oxley empty runs, which back out of Portal - From Bilbrook, faced Forwards where Dovetail's face Backwards:
  the game crashed every time that timetable loaded (EXCEPTION_ACCESS_VIOLATION reading 0xa8, during
  LoadTimetableService). The crash report's GameData section names the timetable and player service.
- A unit turning round (or spawned) at another platform than the template's start gets the route's run of the
  same journey from that platform instead (nearest in time of day; departure time kept), so it leaves from where
  it stands rather than crossing the station first.
- **Unit count matters.** Dovetail's day uses 191 units (1,828 vehicles) for 2,410 services. A unit per portal
  entry gave 1,509 units (~10k vehicles) on the busy day: it froze at the loading screen (0% CPU, no crash).
  With vault reuse: Crewe - MK 83 / 106 units, Birmingham - Crewe 114 / 143 (Dovetail 220), Cardiff 38 / 47
  (Dovetail 78) for typical / busy.
- Leading end after a vault trip: Dovetail's own day follows no fixed rule (a per-portal direction model fits its
  freight 98-99% but its multiple units only 68-78%; e.g. Bletchley Up Fast -> Down Fast swaps 80/84, To
  Northampton -> From Long Buckby keeps 54/54, Bletchley slow 350/1 8-car 30 kept / 32 swapped). Where the stock's
  record (>= 3 trips) or the pair's (>= 10) is >= 90% one way we follow it; otherwise the unit leads as the
  template says, exactly like a new unit from the vault.
- Leading end after a turnround at a station: the unit reverses (the other end leads) unless it leaves by the
  station's far end, as 5F30 -> 1F30 does at Birmingham New Street (in from Coventry, out towards Galton Bridge,
  no swap in Dovetail's day). We used to swap every time. A station's ends are learned from Dovetail's own
  turnrounds: each one puts the place the unit came from (previous station or portal) and the place it heads for
  at the same end (it swapped) or opposite ends (it kept). Chaining these places every neighbour at one end, which
  answers pairs Dovetail never runs. It fits 876 of the 877 turnrounds whose neighbours are known and predicts 87
  of 90 held-out pairs (the misses all come from one odd depot move at New Street). It answers every turnround in
  our six timetables: 19 / 20 carry on in Birmingham - Crewe typical / busy, 5 / 6 in Cardiff, none on
  Crewe - MK. Where it can't tell (no earlier marker, or a move that goes nowhere) the unit reverses.
- Portals are not always named "Portal - ...": Birmingham - Crewe has "Trent Valley Up Fast", "To/From
  Lichfield", "Bilbrook 1/2", "Up/Down Heath Town Portal", "Down/Up Grand Jct (Bushbury)", "Langley Green 1/2",
  and both routes treat "Alsager" as a portal. `runs.vault_markers` finds them from the data: the last stop of
  services whose end formation is bSpawnInVault, and the first stop of services whose start formation is.
- Every vehicle gets a new GUID registered in CompiledRVMap (a unit keeps its ids through its chain).
- Recorded runs stay raw bytes (`engine/tracks.py`): each layer's data track is copied (same name table) with
  only the new services' recordings, Times patched in place. Layers are kept, so they can
  still be toggled in the menu; the master data track is an empty copy.
- Crewe - Milton Keynes typical weekday: 64 patterns -> 1,177 trains, 359 MB mod, built in ~50 s.
- Birmingham - Crewe (`GJL_Gameplay/Timetable/GJR_Weekday_TT`, Dovetail's "2019 Timetable", 2,100 services,
  manifest in the `BirminghamCrewe` plugin) and Cardiff City Network (`CardiffNetwork_Route_Gameplay/Timetable/
  CCC_Timetable_TT`; `_Gen8` is a lighter console copy; the pak's one AssetRegistry.bin sits in the
  `CCC_TFW_Class153` plugin). Pattern minutes past the hour come from Dovetail's own runs.
- Extraction writes `<file>.part` and renames it when complete: a stopped build once left zero-filled data
  tracks that later passed the "already extracted" check.
- App: `python -m tsw_schedule.app` (http://localhost:8765): preview, build, build & install.

## Runtime (UE4SS reflection, TrainSimWorld.exe)
- `RouteTimetable` (actor): `Definition`, `Services`, **`RuntimeSpawnedServices`**, **`RuntimeSpawnedFormations`**,
  `ActiveServices`, many query functions (`GetServiceExpectedTimes`, `GetMarkerNextInstructions`, ...).
  The engine supports adding services at runtime, but no reflected function adds one.
- `TimetableRuntimeExtensionLibrary`: `SpawnFormation(WorldContext, TrainEntry, RibbonLoc, Dir)`,
  `FindSpawnableFormations(Search)`, `FindTrainSpawnLocationNearDriverPawn(...)`: spawning a train is callable.
- `SandboxRouteTimetable` subclass, and `FRouteTimetableService.bSandboxCreatedService`: Free Roam creates real
  AI services at runtime.
- `ASandboxManager` (Free Roam) exposes `SetTrainEntry`, `SetSpawningState`, `IsThereRoomForTrainEntry`,
  `SetDestinationForRV(RailVehicle, RibbonLocation)`, `AddWaypointsForRVPath`, `RemoveCurrentPathForRV`: the
  "add a train, set its path" feature. Untested from a mod, but it is the likely way to dispatch a train at runtime.
- `RouteTimetableLayerDefinition`: one timetable can hold layers, each with its own data track, `RequiredDLC` and
  `ValidMonthRange`.
- Route definitions don't list their timetables; how the menu finds them is still to work out (likely the gameplay
  plugin's `ContentManifest`).
- UE4SS Lua crashes the game on any `ScriptStruct` (`ForEachProperty`/`GetSuperStruct`); classes and enums are fine.
  Structs come from `GenerateSDK()` instead: `dumps/TS2Prototype.hpp`.
