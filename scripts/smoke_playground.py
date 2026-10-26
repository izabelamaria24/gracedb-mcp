"""Live smoke test against the GraceDB PLAYGROUND (anonymous, network required).

Run manually:  python scripts/smoke_playground.py
Not part of the pytest suite (it hits the network).
"""

from __future__ import annotations

import os

os.environ.setdefault("GRACEDB_SERVICE_URL", "https://gracedb-playground.ligo.org/api/")
os.environ.setdefault("GRACEDB_FORCE_NOAUTH", "true")

from gracedb_mcp import server  # noqa: E402  (env must be set before import)


def main() -> None:
    print("== search_superevents (latest 3) ==")
    found = server.search_superevents(max_results=3)
    print("query:", found["query"], "| count:", found["count"])
    for ev in found["superevents"]:
        print(f"  {ev['superevent_id']}  far={ev['far']:.2e}  t_0={ev['t_0']}")

    if not found["superevents"]:
        print("No superevents returned; aborting deeper checks.")
        return

    gid = found["superevents"][0]["superevent_id"]

    print(f"\n== get_event_metadata({gid}) ==")
    meta = server.get_event_metadata(gid)
    print("available_files:", meta["available_files"][:8], "...")
    print("classification:", meta["classification"])
    print("astro_probability:", meta["astro_probability"])


if __name__ == "__main__":
    main()
