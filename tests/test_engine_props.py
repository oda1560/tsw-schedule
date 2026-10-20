"""Round-trip tests for the tagged-property encoder, on assets extracted from the game (skipped if not extracted)."""
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tsw_schedule.engine.package import EditablePackage  # noqa: E402
from tsw_schedule.engine.props import FName, decode_export, encode_export, names_used  # noqa: E402

EXTRACTED = os.path.join(ROOT, "extracted", "TS2Prototype", "Plugins", "DLC")
TIMETABLE = os.path.join(EXTRACTED, "AABS_TrentValley_Gameplay", "Content", "Timetable", "WCMLTT.uasset")
SMALL = [
    os.path.join(EXTRACTED, "WCMLTrentValley", "Content", "ContentManifest.uasset"),
    os.path.join(EXTRACTED, "IOW_V2_Route_Gameplay", "Content", "Timetable", "IOWV2_Timetable_TT.uasset"),
    os.path.join(EXTRACTED, "IOW_V2_Route_Gameplay", "Content", "Timetable", "DataTracks",
                 "IOWV2_Timetable_TT_MasterDataTrack.uasset"),
]


def available(path):
    return os.path.exists(path) and os.path.exists(path[:-len(".uasset")] + ".uexp")


class RoundTrip(unittest.TestCase):
    def test_small_assets_identical(self):
        for path in [p for p in SMALL if available(p)]:
            with self.subTest(os.path.basename(path)):
                pkg = EditablePackage.load(path)
                data = pkg.export_data(1)
                self.assertEqual(encode_export(pkg, decode_export(pkg, data)), data)

    @unittest.skipUnless(available(TIMETABLE), "WCMLTT not extracted")
    def test_edit_with_new_names(self):
        pkg = EditablePackage.load(TIMETABLE)
        export = decode_export(pkg, pkg.export_data(1))
        services = export.props.prop("Services").value.items
        clone = copy.deepcopy(services[0])
        clone.prop("Name").value = FName("9Z99_TEST")
        clone.prop("StartTime").value.ticks += 600 * 10_000_000
        services.append(clone)
        pkg.add_names(names_used(export))
        pkg.replace_export_data(1, encode_export(pkg, export))

        again = decode_export(pkg, pkg.export_data(1)).props.prop("Services").value.items
        self.assertEqual(len(again), len(services))
        self.assertEqual(again[-1]["Name"], FName("9Z99_TEST"))
        self.assertEqual(again[-1]["StartTime"].ticks, services[0]["StartTime"].ticks + 600 * 10_000_000)


if __name__ == "__main__":
    unittest.main()
