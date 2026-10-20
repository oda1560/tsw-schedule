"""Moved to tsw_schedule/engine/package.py; kept so the tools/ scripts keep working.

Usage: python uasset_edit.py <file.uasset> ...   - check name hashes and list summary offsets
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tsw_schedule.engine.package import *  # noqa: E402,F401,F403
from tsw_schedule.engine.package import self_test  # noqa: E402

if __name__ == "__main__":
    for path in sys.argv[1:]:
        self_test(path)
