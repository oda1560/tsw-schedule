# tsw-schedule

Builds new timetables for Train Sim World 7 routes and installs them as mods: a typical weekday, written as a
standard pattern (trains per hour by operator, first/last trains, freight paths), laid onto the paths and recorded
runs of the route's own timetable, with stock the player owns. The new timetable appears next to Dovetail's in the
game's Timetable menu, and you pick your service from the game's own list.

First route: **Crewe - Milton Keynes** (WCML Trent Valley).

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
