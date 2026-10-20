"""Flatten a RouteTimetableDefinition JSON dump (from uasset_dump.py) into one CSV row per station call.

Usage: python timetable_csv.py <timetable.json> <out.csv>
A call is a GoTo to a platform marker followed by its LoadUnload; times are the editor's simulated times.
"""
import csv
import json
import sys


def hms(value):
    seconds = round(value["timespan_s"])
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def last_marker(service):
    names = [i["Destination"].get("Name") for i in service["Instructions"] if i["InstructionType"].endswith("GoTo")]
    return names[-1] if names else None


def main():
    timetable = json.load(open(sys.argv[1], encoding="utf-8"))[0]["properties"]
    # A service that starts where its formation's previous service ended has no GoTo for its first call.
    ended_at = {s["EndOfServiceFormationName"]: last_marker(s) for s in timetable["Services"]}
    with open(sys.argv[2], "w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["service", "friendly_name", "start", "formation", "player_drivable", "call", "marker",
                      "arrive", "depart", "stopping"])
        for service in timetable["Services"]:
            marker, arrive, call = None, None, 0
            for instruction in service["Instructions"]:
                kind = instruction["InstructionType"].split("::")[1]
                if kind == "GoTo":
                    marker = instruction["Destination"].get("Name")
                    arrive = hms(instruction["SimulatedArrivalTime"])
                    if not instruction["bIsStopping"]:
                        call += 1
                        out.writerow([service["Name"], service["FriendlyName"], hms(service["StartTime"]),
                                      service["FormationName"], service["bIsPlayerDrivable"], call, marker,
                                      arrive, "", False])
                elif kind == "LoadUnload":
                    call += 1
                    out.writerow([service["Name"], service["FriendlyName"], hms(service["StartTime"]),
                                  service["FormationName"], service["bIsPlayerDrivable"], call,
                                  marker or ended_at.get(service["FormationName"]) or "(start)",
                                  arrive or hms(instruction["SimulatedArrivalTime"]),
                                  hms(instruction["SimulatedCompletionTime"]), True])
                    marker, arrive = None, None
    print("wrote", sys.argv[2])


if __name__ == "__main__":
    main()
