"""Tests for observing runs, data-quality flags, GPS time and pipeline fields (no network)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from test_catalog import fake_client, gwosc  # noqa: F401 - pytest fixtures

from gracedb_mcp import gpstime, gwosc_catalog
from gracedb_mcp.tools import alerts, observing, strain

RUNS = [
    {"name": "O1", "gps_start": 1126051217, "gps_end": 1137254417, "detectors": ["H1", "L1"],
     "detail_url": "x"},
    {"name": "O3a", "gps_start": 1238166018, "gps_end": 1253977218,
     "detectors": ["H1", "L1", "V1"], "detail_url": "x"},
]
FLAGS = {
    "O3a": [
        {"name": f"{ifo}_{flag}", "description": f"{ifo}: {flag}"}
        for ifo in ("H1", "L1", "V1")
        for flag in ("DATA", "CBC_CAT1", "CBC_CAT2", "NO_CW_HW_INJ", "NO_CBC_HW_INJ")
    ],
}


@pytest.fixture
def runs(monkeypatch, gwosc):  # noqa: F811 - gwosc fixture resets the cache
    monkeypatch.setattr(gwosc_catalog.v2, "fetch_runs", lambda: iter(RUNS))
    monkeypatch.setattr(
        gwosc_catalog.v2, "fetch_run", lambda run: {"doi": f"https://doi.org/x/{run}"}
    )
    monkeypatch.setattr(
        gwosc_catalog.v2,
        "produce_fetched_objects",
        lambda url: iter(FLAGS[url.split("/runs/")[1].split("/")[0]]),
    )


@pytest.fixture
def segments(monkeypatch):
    """Stub flag segments: ``table[flag]`` (default: full coverage of the request)."""
    table: dict = {}
    calls: list = []

    def get_segments(flag, start, end):
        calls.append(flag)
        return table.get(flag, [(start, end)])

    monkeypatch.setattr(observing.timeline, "get_segments", get_segments)
    return table, calls


# -- GPS time ------------------------------------------------------------


@pytest.mark.parametrize(
    ("gps", "utc"),
    [
        (0, "1980-01-06 00:00:00"),
        (1126259462.4, "2015-09-14 09:50:45.400000"),  # GW150914
        (1167264018, "2017-01-01 00:00:00"),  # just after the last leap second (23:59:60)
        (1167264016.5, "2016-12-31 23:59:59.500000"),
    ],
)
def test_gps_utc_round_trip(gps, utc):
    expected = datetime.fromisoformat(utc).replace(tzinfo=timezone.utc)
    assert gpstime.gps_to_utc(gps) == expected
    assert gpstime.utc_to_gps(expected) == pytest.approx(gps)


def test_utc_to_gps_tool_needs_no_gwpy():
    assert strain.utc_to_gps("2015-09-14T09:50:45Z")["gps"] == 1126259462
    assert strain.utc_to_gps("2015-09-14T11:50:45+02:00")["gps"] == 1126259462


# -- Observing runs ------------------------------------------------------


def test_list_observing_runs(runs):
    result = observing.list_observing_runs()
    assert [r["name"] for r in result["runs"]] == ["O1", "O3a"]
    o1 = result["runs"][0]
    assert o1["utc_start"] == "2015-09-12 00:00:00 UTC" and o1["doi"] == "https://doi.org/x/O1"
    assert "detail_url" not in o1
    assert "at_gps" not in result


def test_list_observing_runs_locates_a_time(runs):
    inside = observing.list_observing_runs(gps=1249852257.0)["at_gps"]
    assert inside["run"] == "O3a" and inside["previous_run"] is None
    between = observing.list_observing_runs(gps=1200000000)["at_gps"]
    assert between == {**between, "run": None, "previous_run": "O1", "next_run": "O3a"}


# -- Data quality --------------------------------------------------------


def test_check_data_quality_separates_vetoes_missing_data_and_injections(runs, segments):
    table, calls = segments
    start, end = 1249852000, 1249852100
    table.update(
        H1_DATA=[], H1_CBC_CAT1=[], H1_CBC_CAT2=[], H1_NO_CW_HW_INJ=[], H1_NO_CBC_HW_INJ=[],
        L1_CBC_CAT2=[(start, start + 75)],  # vetoes the last quarter
        L1_NO_CW_HW_INJ=[],  # a CW injection runs throughout
    )
    result = observing.check_data_quality(gps_start=start, gps_end=end, detectors=["H1", "L1"])

    assert result["runs"] == ["O3a"] and result["errors"] == {}
    h1, l1 = result["detectors"]["H1"], result["detectors"]["L1"]
    assert h1["data_coverage"] == 0.0 and h1["failed"] == {} and "note" in h1
    assert l1["data_coverage"] == 1.0 and "note" not in l1
    assert l1["failed"] == {"L1_CBC_CAT2": 0.75}
    assert l1["hardware_injections"] == ["CW"]
    assert not any(flag.startswith("V1_") for flag in calls)  # only requested detectors
    assert result["descriptions"]["L1_CBC_CAT2"] == "L1: CBC_CAT2"


def test_check_data_quality_outside_released_runs(runs, segments):
    result = observing.check_data_quality(gps_start=1460000000, gps_end=1460000100)
    assert result["runs"] == [] and "not public yet" in result["note"]
    assert segments[1] == []


def test_check_data_quality_reports_detectors_not_in_the_run(runs, segments):
    result = observing.check_data_quality(
        gps_start=1249852000, gps_end=1249852100, detectors=["L1", "K1"]
    )
    assert list(result["detectors"]) == ["L1"] and result["not_in_run"] == ["K1"]


def test_check_data_quality_for_an_event(runs, segments, fake_client):  # noqa: F811
    result = observing.check_data_quality(event="S190814bv", window=8)
    assert result["event"] == "S190814bv" and result["runs"] == ["O3a"]


def test_check_data_quality_validates_input(runs, segments):
    assert "error" in observing.check_data_quality()
    assert "error" in observing.check_data_quality(gps_start=0, gps_end=10, detectors=["x/1"])


# -- Pipelines -----------------------------------------------------------

PIPELINE_RECORD = {
    "superevent_id": "S230518h",
    "t_0": 1368449966.17,
    "far": 3.2e-10,
    "preferred_event_data": {"pipeline": "pycbc", "search": "AllSky", "instruments": "H1,L1"},
    "pipeline_preferred_events": {
        "gstlal": {"graceid": "G407383", "search": "AllSky", "far": 1.96e-22,
                   "gpstime": "1368449966.167950", "group": "CBC", "pipeline": "gstlal"},
        "pycbc": {"graceid": "G407378", "search": "AllSky", "far": 3.2e-10,
                  "gpstime": "1368449966.166504", "group": "CBC", "pipeline": "pycbc"},
    },
}


def test_metadata_and_search_report_pipelines(fake_client, monkeypatch):  # noqa: F811
    fake_client.superevents[0] = PIPELINE_RECORD
    meta = alerts.get_event_metadata("S230518h")
    assert meta["preferred"] == {"pipeline": "pycbc", "search": "AllSky", "detectors": ["H1", "L1"]}
    assert meta["pipelines"]["gstlal"] == {
        "graceid": "G407383", "search": "AllSky", "far": 1.96e-22, "gpstime": "1368449966.167950"
    }
    row = alerts.search_superevents()["superevents"][0]
    assert (row["pipeline"], row["detectors"]) == ("pycbc", ["H1", "L1"])


def test_pipelines_missing_before_o4(fake_client):  # noqa: F811
    meta = alerts.get_event_metadata("S230519a")
    assert meta["pipelines"] == {}
    assert meta["preferred"] == {"pipeline": None, "search": None, "detectors": None}
