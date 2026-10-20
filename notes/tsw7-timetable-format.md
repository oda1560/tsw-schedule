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
- Units: portal entries get a new Initial formation in the vault; trains starting on the route take over an
  earlier train's unit of the same stock that ended there (same platform, else same station; >= 8 min), else a
  start-of-day position from Dovetail's own day (once each, departures before 09:00 only), else dropped.
- Every vehicle gets a new GUID registered in CompiledRVMap (a unit keeps its ids through its chain).
- Recorded runs stay raw bytes (`engine/tracks.py`): each layer's data track is copied (same name table) with
  only the new services' recordings, Times patched in place. Layers are kept (RequiredDLC cleared), so they can
  still be toggled in the menu; the master data track is an empty copy.
- Crewe - Milton Keynes typical weekday: 64 patterns -> 1,177 trains, 359 MB mod, built in ~50 s.
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
