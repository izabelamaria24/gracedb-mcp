"""Download H1/L1 strain around catalogued events through the MCP tool.

    python scripts/fetch_training_strain.py                      # the 9 hand-picked events
    python scripts/fetch_training_strain.py --catalog GWTC-3-confident

30 s windows fit inside GWOSC's 32 s event files, so recent events avoid the
~100 MB hour-long downloads.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("GRACEDB_MCP_DATA_DIR", "./data")
os.environ.setdefault("GWPY_CACHE", "1")

from gwosc.datasets import event_gps, find_datasets

from gracedb_mcp import server

EVENTS = [
    "GW150914",
    "GW170104",
    "GW170814",
    "GW190412",
    "GW190521_074359",
    "GW190814",
    "GW191109_010717",
    "GW200129_065458",
    "GW200224_222234",
]
MIN_SECONDS = 30


def have(name: str, ifo: str) -> bool:
    strain = Path(os.environ["GRACEDB_MCP_DATA_DIR"]) / name / "strain"
    return any(int(p.stem.split("-")[-1]) >= MIN_SECONDS for p in strain.glob(f"{ifo}-*.hdf5"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", help="add every event in a GWOSC catalogue, e.g. GWTC-3-confident")
    args = parser.parse_args()

    names = list(EVENTS)
    if args.catalog:
        names += [d.rsplit("-v", 1)[0] for d in find_datasets(type="events", catalog=args.catalog)]

    for name in dict.fromkeys(names):
        missing = [ifo for ifo in ("H1", "L1") if not have(name, ifo)]
        if not missing:
            continue
        result = server.download_strain_data(
            name, detectors=missing, duration=MIN_SECONDS, gps_time=event_gps(name)
        )
        print(f"{name:18s} saved={sorted(result['saved'])} errors={sorted(result['errors']) or '-'}", flush=True)


if __name__ == "__main__":
    main()
