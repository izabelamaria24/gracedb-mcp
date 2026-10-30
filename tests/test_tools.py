"""Unit tests for the GraceDB MCP tools using a fake GraceDB client (no network)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from gracedb_mcp import app, resources, server
from gracedb_mcp.tools import alerts, strain


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

    def get_p_astro(self, superevent_id: str, files=None):
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
        content = b'{"BBH": 0.95}' if filename.endswith(".json") else b"SIMPLE  =  T \x00\x01"
        with open(dest_path, "wb") as f:
            f.write(content)
        return dest_path


@pytest.fixture
def fake_client(monkeypatch, tmp_path):
    client = FakeGraceDbClient()
    monkeypatch.setattr(app, "client", client)
    monkeypatch.setattr(app, "DATA_DIR", str(tmp_path))
    return client


# -- Pure helpers --------------------------------------------------------


def test_build_query_combines_clauses():
    query = alerts._build_query("2023-05-01", "2023-06-01", 1e-8, "Production")
    assert "created: 2023-05-01 .. 2023-06-01" in query
    assert "far < 1e-08" in query
    assert "category: Production" in query


def test_build_query_open_ended():
    # GraceDB rejects "created: X ..", so the missing bound is filled in.
    assert re.fullmatch(
        r"created: 2023-05-01 \.\. \d{4}-\d\d-\d\d \d\d:\d\d:\d\d",
        alerts._build_query("2023-05-01", None, None, ""),
    )
    assert alerts._build_query(None, "2023-06-01", None, "") == "created: 2015-01-01 .. 2023-06-01"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2023-05-18", "2023-05-18"),
        ("2023-05-18T06:30:00", "2023-05-18 06:30:00"),
        ("2023-05-18T06:30:00Z", "2023-05-18 06:30:00"),
        ("2023-05-18T08:30:00+02:00", "2023-05-18 06:30:00"),
        ("2023-05-18 06:30", "2023-05-18 06:30:00"),
    ],
)
def test_gracedb_time_normalises_iso(value, expected):
    assert alerts._gracedb_time(value) == expected


def test_astro_probability():
    assert alerts._astro_probability({"Terrestrial": 0.04}) == 0.96
    assert alerts._astro_probability({"BBH": 1.0}) is None
    assert alerts._astro_probability(None) is None


# -- Tools ---------------------------------------------------------------


def test_search_superevents_basic(fake_client):
    result = alerts.search_superevents(start="2023-05-01", end="2023-06-01")
    assert result["count"] == 2
    assert result["superevents"][0]["superevent_id"] == "S230518h"


def test_search_superevents_min_confidence_filters(fake_client):
    result = alerts.search_superevents(min_confidence=0.9)
    ids = [e["superevent_id"] for e in result["superevents"]]
    assert ids == ["S230518h"]  # S230519a has astro_probability 0.10, filtered out
    assert result["superevents"][0]["astro_probability"] == 0.96


def test_get_event_metadata(fake_client):
    meta = alerts.get_event_metadata("S230518h")
    assert meta["superevent_id"] == "S230518h"
    assert meta["classification"]["BBH"] == 0.95
    assert meta["astro_probability"] == 0.96
    assert "bayestar.fits.gz" in meta["available_files"]


def test_get_event_metadata_lists_files_once(fake_client, monkeypatch):
    calls = []
    list_files = fake_client.list_files
    monkeypatch.setattr(fake_client, "list_files", lambda sid: calls.append(sid) or list_files(sid))
    passed = []
    monkeypatch.setattr(
        fake_client, "get_p_astro", lambda sid, files=None: passed.append(files) or None
    )
    alerts.get_event_metadata("S230518h")
    assert calls == ["S230518h"]
    assert passed and passed[0] is not None  # the listing was reused for p_astro


def test_map_concurrently_keeps_order_and_reports_errors():
    def work(n):
        if n == 3:
            raise RuntimeError("boom")
        return n * n

    results, errors = app.map_concurrently(work, [5, 1, 3, 2, 1])
    assert list(results) == [5, 1, 2]  # input order, duplicates collapsed
    assert results == {5: 25, 1: 1, 2: 4}
    assert list(errors) == [3] and str(errors[3]) == "boom"
    assert app.map_concurrently(work, []) == ({}, {})


def test_download_event_files_reports_each_failure(fake_client, monkeypatch):
    download = fake_client.download_file

    def flaky(sid, name, dest):
        if name == "bayestar.fits.gz":
            raise OSError("connection reset")
        return download(sid, name, dest)

    monkeypatch.setattr(fake_client, "download_file", flaky)
    result = alerts.download_event_files("S230518h")
    assert list(result["saved"]) == ["p_astro.json"]
    assert result["errors"] == {"bayestar.fits.gz": "connection reset"}


def test_real_client_streams_downloads(tmp_path, monkeypatch):
    import io

    from gracedb_mcp.gracedb_client import GraceDbClient

    reads: list[int] = []

    class Response(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    client = GraceDbClient()
    body = b"x" * (3 * 1024 * 1024)
    monkeypatch.setattr(client, "_client", type("G", (), {"files": lambda *_: Response(body)})())
    dest = client.download_file("S1", "big.hdf5", str(tmp_path / "big.hdf5"))
    assert (tmp_path / "big.hdf5").read_bytes() == body and dest.endswith("big.hdf5")
    assert reads and -1 not in reads  # never read the whole body at once


def test_download_event_files_specific(fake_client):
    result = alerts.download_event_files("S230518h", filenames=["p_astro.json"])
    assert "p_astro.json" in result["saved"]
    assert result["errors"] == {}
    assert os.path.basename(result["saved"]["p_astro.json"]) == "p_astro.json"


def test_download_event_files_missing(fake_client):
    result = alerts.download_event_files("S230518h", filenames=["nope.json"])
    assert "nope.json" in result["errors"]
    assert result["saved"] == {}


@pytest.mark.parametrize("bad_id", ["../etc", "a/b", "", "x" * 65])
def test_rejects_unsafe_event_ids(fake_client, bad_id):
    with pytest.raises(ValueError):
        alerts.download_event_files(bad_id)
    with pytest.raises(ValueError):
        strain.download_strain_data(bad_id, gps_time=1126259462.4)


def test_download_strain_data_gps_time_skips_gracedb(fake_client, monkeypatch):
    pytest.importorskip("gwpy")
    calls = []

    class FakeSeries:
        def write(self, dest, overwrite=False):
            open(dest, "wb").close()

    def fake_fetch(ifo, start, end, sample_rate):
        calls.append((ifo, start, end))
        return FakeSeries()

    monkeypatch.setattr(strain, "_fetch_strain", fake_fetch)
    # GW150914 is not a superevent; the fake client would raise KeyError if queried.
    result = strain.download_strain_data("GW150914", duration=4, gps_time=1126259462.4)

    assert result["t_0"] == 1126259462.4
    assert result["gps_window"] == [1126259460.4, 1126259464.4]
    assert set(result["saved"]) == {"H1", "L1"}
    assert [c[0] for c in calls] == ["H1", "L1"]


def test_pick_gwosc_file_prefers_shortest_covering_file():
    hour = "https://gwosc.org/archive/H-H1_GWOSC_4KHZ_R1-1256777520-4096.hdf5"
    event = "https://gwosc.org/eventapi/H-H1_GWOSC_4KHZ_R1-1256779552-32.hdf5"
    assert strain._pick_gwosc_file([hour, event], 1256779553.0, 1256779583.0) == event
    # Window starts before the 32 s file, so only the hour file covers it.
    assert strain._pick_gwosc_file([hour, event], 1256779551.5, 1256779581.5) == hour
    assert strain._pick_gwosc_file([event], 1256779500.0, 1256779530.0) is None
    assert strain._pick_gwosc_file(["https://example/not-a-gwosc-name.hdf5"], 0, 1) is None


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


def test_downloaded_files_are_readable_resources(fake_client):
    import base64

    import anyio
    from mcp.client.client import Client

    async def scenario():
        async with Client(server.mcp) as client:
            async with client.listen(resources_list_changed=True) as events:
                call = await client.call_tool("download_event_files", {"graceid": "S230518h"})
                with anyio.fail_after(5):
                    event = await anext(aiter(events))
            uris = call.structured_content["resources"]
            listed = {str(r.uri) for r in (await client.list_resources()).resources}
            text = await client.read_resource(uris["p_astro.json"])
            blob = await client.read_resource(uris["bayestar.fits.gz"])
            return event, uris, listed, text.contents[0], blob.contents[0]

    event, uris, listed, text, blob = anyio.run(scenario)

    assert type(event).__name__ == "ResourcesListChanged"
    assert uris == {
        "p_astro.json": "gracedb://files/S230518h/p_astro.json",
        "bayestar.fits.gz": "gracedb://files/S230518h/bayestar.fits.gz",
    }
    assert set(uris.values()) <= listed
    assert text.mime_type == "application/json" and text.text == '{"BBH": 0.95}'
    assert blob.mime_type == "application/gzip"
    assert base64.b64decode(blob.blob) == b"SIMPLE  =  T \x00\x01"


def test_large_and_hidden_files_are_not_resources(fake_client, tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "MAX_RESOURCE_BYTES", 4)
    result = alerts.download_event_files("S230518h", filenames=["p_astro.json"])
    assert result["saved"] and result["resources"] == {}
    hidden = tmp_path / ".DS_Store"
    hidden.write_bytes(b"")
    assert resources.register_file(str(hidden)) is None


def test_existing_downloads_are_registered_at_startup(fake_client, tmp_path):
    strain = tmp_path / "GW150914" / "strain"
    strain.mkdir(parents=True)
    (strain / "H1-1126259446-32.hdf5").write_bytes(b"\x89HDF")
    resources.register_existing_files()

    import anyio

    registered = {str(r.uri): r for r in anyio.run(server.mcp.list_resources)}
    resource = registered["gracedb://files/GW150914/strain/H1-1126259446-32.hdf5"]
    assert resource.mime_type == "application/x-hdf5"


@pytest.mark.parametrize(
    ("name", "mime"),
    [
        ("bayestar.multiorder.fits,0", "application/fits"),
        ("p_astro.json,1", "application/json"),
        ("bayestar.png", "image/png"),
        ("something.unknown", "application/octet-stream"),
    ],
)
def test_mime_type_handles_versioned_names(name, mime):
    assert resources.mime_type(name) == mime


# -- Prompts -------------------------------------------------------------


def _prompt_text(name, arguments):
    """Render a prompt through a real client; returns its text, or the MCPError raised."""
    import anyio
    from mcp.client.client import Client
    from mcp.shared.exceptions import MCPError

    async def render():
        async with Client(server.mcp) as client:
            try:
                result = await client.get_prompt(name, arguments)
            except MCPError as error:
                return error
            return result.messages[0].content.text

    return anyio.run(render)


# Result fields the prompts mention; every other backticked snake_case name must be a tool.
PROMPT_FIELDS = {"available_files", "gracedb_id", "p_astro"}


PROMPT_CASES = [
    ("alert_briefing", {}),
    ("alert_briefing", {"hours": "6", "max_far": "1e-8"}),
    ("event_dossier", {"event": "GW190814"}),
    ("event_dossier", {"event": "S190814bv", "audience": "public"}),
    ("alert_vs_catalog", {"event": "S190814bv"}),
    ("alert_vs_catalog", {"catalog": "GWTC-3-confident", "max_events": "5"}),
]


def test_prompts_are_listed():
    import anyio

    names = {p.name for p in anyio.run(server.mcp.list_prompts)}
    assert names == {"alert_briefing", "event_dossier", "alert_vs_catalog"}


@pytest.mark.parametrize(("name", "arguments"), PROMPT_CASES)
def test_prompts_only_name_existing_tools(name, arguments):
    import anyio

    tools = {t.name for t in anyio.run(server.mcp.list_tools)}
    text = _prompt_text(name, arguments)
    named = {word for word in re.findall(r"`([a-z_]+)`", text) if "_" in word} - PROMPT_FIELDS
    assert named, "prompt should name the tools to call"
    assert named <= tools, f"unknown tools: {named - tools}"


def test_alert_briefing_uses_a_closed_window_gracedb_accepts():
    text = _prompt_text("alert_briefing", {"hours": "6"})
    window = re.search(r'start="([^"]+)", end="([^"]+)"', text)
    start, end = (
        datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        for v in window.groups()
    )
    assert end - start == timedelta(hours=6)


def test_prompt_arguments_reach_the_text():
    assert "GWTC-3-confident" in _prompt_text("alert_vs_catalog", {"catalog": "GWTC-3-confident"})
    assert "no jargon" in _prompt_text("event_dossier", {"event": "GW150914", "audience": "public"})


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("event_dossier", {"event": "../etc"}, "invalid event name"),
        ("event_dossier", {"event": "GW150914", "audience": "cats"}, "audience must be"),
        ("alert_vs_catalog", {}, "exactly one of event or catalog"),
        (
            "alert_vs_catalog",
            {"event": "S190814bv", "catalog": "GWTC-3-confident"},
            "exactly one of event or catalog",
        ),
    ],
)
def test_prompts_reject_bad_arguments_clearly(name, arguments, message):
    from mcp.shared.exceptions import MCPError

    error = _prompt_text(name, arguments)
    assert isinstance(error, MCPError)
    assert message in str(error)
