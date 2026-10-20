"""Shift whole services in a cooked TSW timetable (and their recorded runs in its data tracks) by a fixed time.

Usage:
  python shift_services.py --extracted <dir> --out <dir> --minutes <n> --services <name> [<name> ...]
                           --timetable <rel/path.uasset> --datatracks <rel/path.uasset> [...]

Paths are relative to --extracted (the pak's file tree, as written by paklist.py --extract); patched copies land at
the same relative paths under --out, ready to pack. Clock times are 8-byte FTimespan values, so they are rewritten in
place: file sizes and every other byte stay the same.
"""
import argparse
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uasset_dump import TICKS_PER_SECOND, load  # noqa: E402

# Times of day. Durations (MinDwellTime, WaitingTime, tolerances, LoadingCriteria.MinimumTime) stay as they are.
CLOCK_FIELDS = {"StartTime", "SimulatedStartTime", "ArrivalTime", "SimulatedArrivalTime", "CompletionTime",
                "SimulatedCompletionTime", "Time"}


def clock_offsets(node, found):
    """Offsets of every set (non-zero) clock time under node."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, dict) and "@" in value:
                if key in CLOCK_FIELDS and value["timespan_s"] > 0:
                    found.append(value["@"])
            else:
                clock_offsets(value, found)
    elif isinstance(node, list):
        for value in node:
            clock_offsets(value, found)
    return found


def patch(body, offsets, delta_ticks):
    body = bytearray(body)
    for offset in offsets:
        ticks = struct.unpack_from("<q", body, offset)[0]
        struct.pack_into("<q", body, offset, ticks + delta_ticks)
    return bytes(body)


def write_pair(extracted, out, rel, body):
    src = os.path.join(extracted, rel)
    dst = os.path.join(out, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    with open(dst[:-len(".uasset")] + ".uexp", "wb") as f:
        f.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extracted", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, required=True)
    ap.add_argument("--services", nargs="+", required=True)
    ap.add_argument("--timetable", required=True)
    ap.add_argument("--datatracks", nargs="*", default=[])
    args = ap.parse_args()
    delta = int(args.minutes * 60 * TICKS_PER_SECOND)
    wanted = set(args.services)

    exports, _, body = load(os.path.join(args.extracted, args.timetable), with_offsets=True)
    services = [s for s in exports[0]["properties"]["Services"] if s["Name"] in wanted]
    missing = wanted - {s["Name"] for s in services}
    if missing:
        sys.exit(f"not in the timetable: {sorted(missing)}")
    offsets = []
    for service in services:
        n = len(offsets)
        clock_offsets(service, offsets)
        print(f"timetable  {service['Name']}: {len(offsets) - n} times")
    write_pair(args.extracted, args.out, args.timetable, patch(body, offsets, delta))

    for rel in args.datatracks:
        exports, _, body = load(os.path.join(args.extracted, rel), with_offsets=True)
        offsets = []
        for name, track in exports[0]["properties"].get("ServiceDataTracks", []):
            if name in wanted:
                n = len(offsets)
                clock_offsets(track, offsets)
                print(f"datatrack  {os.path.basename(rel)} {name}: {len(offsets) - n} times")
        if offsets:
            write_pair(args.extracted, args.out, rel, patch(body, offsets, delta))
        else:
            print(f"datatrack  {os.path.basename(rel)}: none of the services, left out")


if __name__ == "__main__":
    main()
