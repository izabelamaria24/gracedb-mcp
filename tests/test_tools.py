"""Unit tests for the GraceDB MCP tools using a fake GraceDB client (no network)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from gracedb_mcp import server


class FakeGraceDbClient:
    """Stand-in for GraceDbClient with canned responses."""

    def __init__(self) -> None:
        self.superevents = [
            {
                "superevent_id": "S230518h",
                "created": "2023-05-18T01:00:00Z",
                "t_0": 1368327408.0,
                "far": 1e-9,
                "preferred_event": "G123456",
                "labels": ["EM_READY"],
            },
            {
                "superevent_id": "S230519a",
                "created": "2023-05-19T02:00:00Z",
                "t_0": 1368414000.0,
                "far": 5e-7,
                "preferred_event": "G123457",
                "labels": [],
            },
        ]
        self.p_astro_by_id = {
            "S230518h": {"BBH": 0.95, "NSBH": 0.01, "BNS": 0.0, "Terrestrial": 0.04},
            "S230519a": {"BBH": 0.10, "NSBH": 0.0, "BNS": 0.0, "Terrestrial": 0.90},
        }
        self.downloaded: list[tuple[str, str, str]] = []

    def search_superevents(self, query: str, *, max_results: int = 20, columns=None):
        return self.superevents[:max_results]

    def get_p_astro(self, superevent_id: str):
        return self.p_astro_by_id.get(superevent_id)

    def get_superevent(self, superevent_id: str) -> dict[str, Any]:
        for record in self.superevents:
            if record["superevent_id"] == superevent_id:
                return record
        raise KeyError(superevent_id)

    def list_files(self, superevent_id: str) -> dict[str, str]:
        return {
            "bayestar.fits.gz": "https://example/bayestar.fits.gz",
            "p_astro.json": "https://example/p_astro.json",
        }

    def download_file(self, superevent_id: str, filename: str, dest_path: str) -> str:
        self.downloaded.append((superevent_id, filename, dest_path))
        return dest_path


@pytest.fixture
def fake_client(monkeypatch, tmp_path):
    client = FakeGraceDbClient()
    monkeypatch.setattr(server, "_client", client)
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    return client


# -- Pure helpers --------------------------------------------------------


def test_build_query_combines_clauses():
    query = server._build_query("2023-05-01", "2023-06-01", 1e-8, "Production")
    assert "created: 2023-05-01 .. 2023-06-01" in query
    assert "far < 1e-08" in query
    assert "category: Production" in query


def test_build_query_open_ended():
    assert server._build_query("2023-05-01", None, None, "") == "created: 2023-05-01 .."
    assert server._build_query(None, "2023-06-01", None, "") == "created: .. 2023-06-01"


def test_astro_probability():
    assert server._astro_probability({"Terrestrial": 0.04}) == 0.96
    assert server._astro_probability({"BBH": 1.0}) is None
    assert server._astro_probability(None) is None


# -- Tools ---------------------------------------------------------------


def test_search_superevents_basic(fake_client):
    result = server.search_superevents(start="2023-05-01", end="2023-06-01")
    assert result["count"] == 2
    assert result["superevents"][0]["superevent_id"] == "S230518h"


def test_search_superevents_min_confidence_filters(fake_client):
    result = server.search_superevents(min_confidence=0.9)
    ids = [e["superevent_id"] for e in result["superevents"]]
    assert ids == ["S230518h"]  # S230519a has astro_probability 0.10, filtered out
    assert result["superevents"][0]["astro_probability"] == 0.96


def test_get_event_metadata(fake_client):
    meta = server.get_event_metadata("S230518h")
    assert meta["superevent_id"] == "S230518h"
    assert meta["classification"]["BBH"] == 0.95
    assert meta["astro_probability"] == 0.96
    assert "bayestar.fits.gz" in meta["available_files"]


def test_download_event_files_specific(fake_client):
    result = server.download_event_files("S230518h", filenames=["p_astro.json"])
    assert "p_astro.json" in result["saved"]
    assert result["errors"] == {}
    assert os.path.basename(result["saved"]["p_astro.json"]) == "p_astro.json"


def test_download_event_files_missing(fake_client):
    result = server.download_event_files("S230518h", filenames=["nope.json"])
    assert "nope.json" in result["errors"]
    assert result["saved"] == {}


@pytest.mark.parametrize("bad_id", ["../etc", "a/b", "", "x" * 65])
def test_rejects_unsafe_event_ids(fake_client, bad_id):
    with pytest.raises(ValueError):
        server.download_event_files(bad_id)
    with pytest.raises(ValueError):
        server.download_strain_data(bad_id, gps_time=1126259462.4)


def test_download_strain_data_gps_time_skips_gracedb(fake_client, monkeypatch):
    pytest.importorskip("gwpy")
    calls = []

    class FakeSeries:
        def write(self, dest, overwrite=False):
            open(dest, "wb").close()

    def fake_fetch(ifo, start, end, sample_rate):
        calls.append((ifo, start, end))
        return FakeSeries()

    monkeypatch.setattr(server, "_fetch_strain", fake_fetch)
    # GW150914 is not a superevent; the fake client would raise KeyError if queried.
    result = server.download_strain_data("GW150914", duration=4, gps_time=1126259462.4)

    assert result["t_0"] == 1126259462.4
    assert result["gps_window"] == [1126259460.4, 1126259464.4]
    assert set(result["saved"]) == {"H1", "L1"}
    assert [c[0] for c in calls] == ["H1", "L1"]


def test_pick_gwosc_file_prefers_shortest_covering_file():
    hour = "https://gwosc.org/archive/H-H1_GWOSC_4KHZ_R1-1256777520-4096.hdf5"
    event = "https://gwosc.org/eventapi/H-H1_GWOSC_4KHZ_R1-1256779552-32.hdf5"
    assert server._pick_gwosc_file([hour, event], 1256779553.0, 1256779583.0) == event
    # Window starts before the 32 s file, so only the hour file covers it.
    assert server._pick_gwosc_file([hour, event], 1256779551.5, 1256779581.5) == hour
    assert server._pick_gwosc_file([event], 1256779500.0, 1256779530.0) is None
    assert server._pick_gwosc_file(["https://example/not-a-gwosc-name.hdf5"], 0, 1) is None


def test_server_reports_package_version():
    import importlib.metadata

    import anyio
    from mcp.client.client import Client

    async def server_info():
        async with Client(server.mcp) as client:
            return client.server_info

    info = anyio.run(server_info)
    assert info.name == "gracedb-mcp"
    assert info.version == importlib.metadata.version("gracedb-mcp")
