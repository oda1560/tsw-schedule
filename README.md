# tsw-schedule

Builds new timetables for Train Sim World 7 routes and installs them as mods: a typical weekday, written as a
standard pattern (trains per hour by operator, first/last trains, freight paths), laid onto the paths and recorded
runs of the route's own timetable, with stock the player owns. The new timetable appears next to Dovetail's in the
game's Timetable menu, and you pick your service from the game's own list.

Routes, each with a **typical weekday** (a standard-hour pattern) and a **busy weekday** (peak-level frequencies
all day, more freight):

| Route | Typical | Busy |
|---|---|---|
| **Crewe - Milton Keynes** (WCML Trent Valley) | `crewe-mk-typical`, ~1,180 trains | `crewe-mk-busy`, ~1,870 trains |
| **Birmingham - Crewe** | `bham-crewe-typical`, ~1,260 trains | `bham-crewe-busy`, ~1,590 trains |
| **Cardiff City Network** | `cardiff-typical`, ~740 trains | `cardiff-busy`, ~920 trains |

## Using it
```
python -m tsw_schedule.app
```
Open http://localhost:8765, pick the route and option, **Preview** to see every train (stock swaps, turnrounds,
trains bunching at a portal), then **Build & install**. Restart the game and choose the timetable in Timetable
mode. Each option is its own mod (`TSWS_<Option>.pak`), so several can be installed at once.

To take one out of the game, press **Remove** next to it in the app (it moves to `build\removed-mods`, so it can
be put back), or delete the `.pak` from `Documents\My Games\TrainSimWorld7\Saved\UserContent` with the game
closed. Command line: `python -m tsw_schedule.build <route> --pattern <option> [--install]`, e.g.
`python -m tsw_schedule.build cardiff --pattern cardiff-busy --install` (routes: `crewe-mk`, `bham-crewe`,
`cardiff`).

## How it works
- `tsw_schedule/engine/`: reads and writes TSW7's cooked timetable assets losslessly (`props.py`, `package.py`),
  groups a timetable's services into runs per train (`runs.py`), generates a new day by cloning runs at new times
  with new formations and vehicle ids (`generate.py`), and packages it as a mod with its own ContentManifest and
  AssetRegistry entry (`modpack.py`, `pak.py`, `assetregistry.py`).
- `tools/`: command-line helpers (list/extract paks, dump assets to JSON, etc.).
- `ue4ss-mods/TimetableProbe/`: a UE4SS Lua mod that logs which timetables the game loaded (for testing).
- `notes/tsw7-timetable-format.md`: what we know about the formats and how the game loads mods.

Game files are never committed: extract them locally from your own install with `tools/paklist.py`.

## Requirements
Python 3.12 (standard library only), TSW7 on PC with **Settings > Advanced > Enable Mods** turned on; mods are
installed to `Documents\My Games\TrainSimWorld7\Saved\UserContent`.
