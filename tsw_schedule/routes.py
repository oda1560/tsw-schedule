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
    "bham-crewe": {
        "name": "Birmingham - Crewe",
        "pak": os.path.join(GAME, "TS2Prototype-WindowsNoEditor-BirminghamCrewe.pak"),
        "dlc": "BirminghamCrewe",
        "timetable": "TS2Prototype/Plugins/DLC/GJL_Gameplay/Content/Timetable/GJR_Weekday_TT.uasset",
        "master_track": "TS2Prototype/Plugins/DLC/GJL_Gameplay/Content/Timetable/DataTracks/"
                        "GJR_Weekday_TT_MasterDataTrack.uasset",
        "manifest_template": "TS2Prototype/Plugins/DLC/BirminghamCrewe/Content/ContentManifest.uasset",
        "registry_template": "TS2Prototype/Plugins/DLC/BirminghamCrewe/Content/AssetRegistry.bin",
    },
    "cardiff": {
        "name": "Cardiff City Network",
        "pak": os.path.join(GAME, "TS2Prototype-WindowsNoEditor-CardiffCityCommuter.pak"),
        "dlc": "CardiffCityCommuter",
        "timetable": "TS2Prototype/Plugins/DLC/CardiffNetwork_Route_Gameplay/Content/Timetable/CCC_Timetable_TT.uasset",
        "master_track": "TS2Prototype/Plugins/DLC/CardiffNetwork_Route_Gameplay/Content/Timetable/DataTracks/"
                        "CCC_Timetable_TT_MasterDataTrack.uasset",
        "manifest_template": "TS2Prototype/Plugins/DLC/CardiffNetwork/Content/ContentManifest.uasset",
        # the pak's one registry (covers every plugin in it) sits in the Class 153 plugin
        "registry_template": "TS2Prototype/Plugins/DLC/CCC_TFW_Class153/Content/AssetRegistry.bin",
    },
}
