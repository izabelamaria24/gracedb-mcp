"""Download real LIGO glitches labelled by Gravity Spy, through the MCP tool.

    python scripts/fetch_glitches.py --files 30

Glitch times are only available inside hour-long GWOSC files (~100 MB each), so
glitches are chosen to cover many types from few files. GWPY_CACHE=1 (set below)
downloads each hour file once; it lives in the astropy cache afterwards.

Hours already in the previous manifest are reused in full (no download cost), and
GWOSC data-availability segments are checked first so no clip contains a gap.
Use --dry-run to see what would be fetched.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import urllib.request
from pathlib import Path

from gw_rl.data import F_HIGH, F_LOW

ZENODO_RECORD = "https://zenodo.org/api/records/1486046"  # Gravity Spy training set v1.1
METADATA = "trainingset_v1d1_metadata.csv"
SKIP_LABELS = {"No_Glitch", "None_of_the_Above", "Chirp"}  # "Chirp" = hardware-injected signals
GWOSC_FILE_SECONDS = 4096
CLIP_SECONDS = 30


def download_metadata(dest: Path) -> None:
    with urllib.request.urlopen(ZENODO_RECORD, timeout=60) as response:
        files = json.load(response)["files"]
    url = next(f["links"]["self"] for f in files if f["key"] == METADATA)
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)


def block_of(row: dict) -> tuple[str, int]:
    return row["ifo"], int(float(row["event_time"])) // GWOSC_FILE_SECONDS


def with_data(key: tuple[str, int], rows: list[dict]) -> list[dict]:
    """Keep rows whose whole clip is covered by GWOSC data (one request per hour)."""
    from gwosc.timeline import get_segments

    ifo, block = key
    start = block * GWOSC_FILE_SECONDS
    try:
        segments = get_segments(f"{ifo}_DATA", start, start + GWOSC_FILE_SECONDS)
    except Exception as exc:  # network trouble: treat the hour as unavailable
        print(f"  availability check failed for {key}: {exc}", flush=True)
        return []
    half = CLIP_SECONDS / 2
    return [
        r for r in rows
        if any(s <= float(r["event_time"]) - half and float(r["event_time"]) + half <= e for s, e in segments)
    ]


def choose(rows: list[dict], n_files: int, cap: int, cached: set) -> tuple[list[dict], int]:
    """Take every glitch in ``cached`` hours, then greedily add ``n_files`` new hours.

    New hours are ranked by how many glitches of types still under ``cap`` they add.
    Availability is checked lazily, only for hours that reach the top of the ranking.
    Returns the chosen rows and the number of new hour files.
    """
    blocks = collections.defaultdict(list)
    for row in rows:
        blocks[block_of(row)].append(row)

    have: collections.Counter = collections.Counter()
    chosen = []
    for key in sorted(cached & blocks.keys()):
        for row in with_data(key, blocks.pop(key)):
            have[row["label"]] += 1
            chosen.append(row)

    def gain(key) -> int:
        counts = collections.Counter(r["label"] for r in blocks[key])
        return sum(min(n, cap - have[label]) for label, n in counts.items() if have[label] < cap)

    checked: set = set()
    new_files = 0
    while new_files < n_files and blocks:
        key = max(blocks, key=gain)
        if gain(key) == 0:
            break
        if key not in checked:  # re-rank after dropping clips without data
            checked.add(key)
            blocks[key] = with_data(key, blocks[key])
            continue
        for row in blocks.pop(key):
            if have[row["label"]] < cap:
                have[row["label"]] += 1
                chosen.append(row)
        new_files += 1
    return chosen, new_files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/glitches")
    parser.add_argument("--metadata", default=f"data/gravityspy/{METADATA}")
    parser.add_argument("--files", type=int, default=30, help="new hour files to download (~100 MB each)")
    parser.add_argument("--cap", type=int, default=60, help="max glitches per type from new hours")
    parser.add_argument("--dry-run", action="store_true", help="show the selection, download nothing")
    args = parser.parse_args()

    metadata = Path(args.metadata)
    if not metadata.exists():
        download_metadata(metadata)

    half = CLIP_SECONDS / 2
    rows = [
        r
        for r in csv.DictReader(metadata.open())
        if r["label"] not in SKIP_LABELS
        and F_LOW <= float(r["peak_frequency"]) <= F_HIGH
        # Keep the clip inside one hour file so each glitch costs at most one download.
        and half < float(r["event_time"]) % GWOSC_FILE_SECONDS < GWOSC_FILE_SECONDS - half
    ]
    out = Path(args.out)
    previous = out / "manifest.csv"
    cached = {block_of({"ifo": r["ifo"], "event_time": r["gps"]}) for r in csv.DictReader(previous.open())} \
        if previous.exists() else set()

    chosen, new_files = choose(rows, args.files, args.cap, cached)
    chosen.sort(key=lambda r: float(r["event_time"]))
    print(f"{len(chosen)} glitches: {len(cached)} cached hours + {new_files} new (~{new_files * 100} MB)",
          flush=True)
    print(collections.Counter(r["label"] for r in chosen).most_common(), flush=True)
    if args.dry_run:
        return

    os.environ.setdefault("GWPY_CACHE", "1")
    os.environ["GRACEDB_MCP_DATA_DIR"] = str(out)
    from gracedb_mcp import server

    manifest = []
    for i, row in enumerate(chosen, 1):
        gid, ifo = row["gravityspy_id"], row["ifo"]
        existing = sorted((out / gid / "strain").glob(f"{ifo}-*.hdf5"))
        if existing:
            path = str(existing[0])
        else:
            result = server.download_strain_data(
                gid, detectors=[ifo], duration=CLIP_SECONDS, gps_time=float(row["event_time"])
            )
            if ifo not in result["saved"]:
                print(f"  skip {gid}: {result['errors']}", flush=True)
                continue
            path = result["saved"][ifo]
        manifest.append(
            {"id": gid, "ifo": ifo, "label": row["label"], "gps": row["event_time"],
             "snr": row["snr"], "peak_frequency": row["peak_frequency"], "path": path}
        )
        if i % 25 == 0:
            print(f"  {i}/{len(chosen)}", flush=True)

    with (out / "manifest.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    print(f"wrote {len(manifest)} glitches to {out / 'manifest.csv'}")
    print(collections.Counter(m["label"] for m in manifest).most_common())


if __name__ == "__main__":
    main()
