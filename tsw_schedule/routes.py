"""Routes tsw-schedule can build timetables for: where their files are in the game and in the extracted tree."""
import os

GAME = r"F:\SteamLibrary\steamapps\common\Train Sim World 7\WindowsNoEditor\TS2Prototype\Content\DLC"
USER_CONTENT = os.path.join(os.path.expanduser("~"), "OneDrive", "Documents", "My Games", "TrainSimWorld7",
                            "Saved", "UserContent")
if not os.path.isdir(os.path.dirname(USER_CONTENT)):
    USER_CONTENT = os.path.join(os.path.expanduser("~"), "Documents", "My Games", "TrainSimWorld7", "Saved",
                                "UserContent")

ROUTES = {
    "crewe-mk": {
        "name": "Crewe - Milton Keynes",
        "pak": os.path.join(GAME, "TS2Prototype-WindowsNoEditor-WCMLTrentValleyCreweMiltonKeynes.pak"),
        "dlc": "WCMLTrentValleyCreweMiltonKeynes",
        "timetable": "TS2Prototype/Plugins/DLC/AABS_TrentValley_Gameplay/Content/Timetable/WCMLTT.uasset",
        "master_track": "TS2Prototype/Plugins/DLC/AABS_TrentValley_Gameplay/Content/Timetable/DataTracks/"
                        "WCMLTT_MasterDataTrack.uasset",
        "manifest_template": "TS2Prototype/Plugins/DLC/WCMLTrentValley/Content/ContentManifest.uasset",
        "registry_template": "TS2Prototype/Plugins/DLC/WCMLTrentValley/Content/AssetRegistry.bin",
    },
}
