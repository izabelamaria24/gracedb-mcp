"""Tests for the GWOSC catalogue and GraceDB label tools (GWOSC API stubbed, no network)."""

from __future__ import annotations

import pytest
from test_tools import FakeGraceDbClient

from gracedb_mcp import gwosc_catalog, server


def _record(name, version, catalog, gps, **params):
    record = {"commonName": name, "version": version, "catalog.shortName": catalog, "GPS": gps}
    record.update(params)
    return record


ALL_EVENTS = {
    "GW150914-v3": _record(
        "GW150914", 3, "GWTC-1-confident", 1126259462.4,
        total_mass_source=63.1, total_mass_source_lower=-3.0, total_mass_source_upper=3.3,
        luminosity_distance=440.0, network_matched_filter_snr=24.4, p_astro=1.0,
    ),
    "GW190814-v1": _record(
        "GW190814", 1, "GWTC-2", 1249852257.0,
        total_mass_source=25.8, luminosity_distance=241.0, network_matched_filter_snr=25.0,
    ),
    "GW190814-v2": _record(
        "GW190814", 2, "GWTC-2.1-confident", 1249852257.0,
        total_mass_source=25.9, luminosity_distance=230.0, network_matched_filter_snr=25.3,
        p_astro=1.0,
    ),
    "GW200105_162426-v2": _record(
        "GW200105_162426", 2, "GWTC-3-confident", 1262276684.0,
        total_mass_source=11.0, luminosity_distance=270.0, network_matched_filter_snr=13.7,
        p_astro=0.36,
    ),
}

FULL_RECORD = {
    **ALL_EVENTS["GW190814-v2"],
    "mass_1_source": 23.2,
    "mass_1_source_lower": -1.0,
    "mass_1_source_upper": 1.1,
    "gracedb_id": "S190814bv",
    "strain": [
        {"detector": "L1", "duration": 4096, "sampling_rate": 4096, "format": "hdf5"},
        {"detector": "L1", "duration": 4096, "sampling_rate": 4096, "format": "gwf"},
        {"detector": "H1", "duration": 32, "sampling_rate": 16384, "format": "hdf5"},
    ],
    "parameters": {"pe_b": {}, "pe_a": {}},
}


@pytest.fixture
def gwosc(monkeypatch):
    monkeypatch.setattr(gwosc_catalog, "_cache", {})
    monkeypatch.setattr(
        gwosc_catalog.api, "fetch_allevents_json", lambda: {"events": dict(ALL_EVENTS)}
    )

    def fetch_event_json(name):
        if name != "GW190814":
            raise ValueError(f"failed to identify catalog for event {name!r}")
        return {"events": {"GW190814-v2": FULL_RECORD}}

    monkeypatch.setattr(gwosc_catalog.api, "fetch_event_json", fetch_event_json)


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeGraceDbClient()
    client.superevents.append({"superevent_id": "S190814bv", "t_0": 1249852257.01})
    client.get_labels = lambda sid: [
        {"name": "PE_READY", "created": "2019-08-15 10:00:00 UTC", "creator": "a@b.org"},
        {"name": "DQOK", "created": "2019-08-14 21:40:00 UTC", "creator": "a@b.org"},
        {"name": "MYSTERY", "created": "2019-08-16 00:00:00 UTC", "creator": "a@b.org"},
    ]
    monkeypatch.setattr(server, "_client", client)
    return client


def test_all_events_keeps_newest_version(gwosc):
    names = [r["commonName"] for r in gwosc_catalog.all_events()]
    assert names == ["GW200105_162426", "GW190814", "GW150914"]  # newest first, de-duplicated
    assert gwosc_catalog.all_events()[1]["version"] == 2


def test_all_events_filters_catalog_before_versions(gwosc):
    # GW190814's newest version is in GWTC-2.1, but it must still show up in GWTC-2.
    events = gwosc_catalog.all_events("GWTC-2")
    assert [(r["commonName"], r["version"]) for r in events] == [("GW190814", 1)]


def test_search_catalog_events_filters(gwosc):
    result = server.search_catalog_events(min_snr=20, max_distance_mpc=300)
    assert [e["name"] for e in result["events"]] == ["GW190814"]

    result = server.search_catalog_events(min_total_mass=20, max_results=1)
    assert result["count"] == 2
    assert len(result["events"]) == 1

    # Events missing the filtered parameter are excluded.
    result = server.search_catalog_events(catalog="GWTC-2", min_p_astro=0.5)
    assert result["count"] == 0


def test_search_catalog_events_unknown_catalog(gwosc):
    result = server.search_catalog_events(catalog="GWTC-99")
    assert "error" in result
    assert "GWTC-3-confident" in result["catalogs"]


def test_summarise_intervals():
    summary = gwosc_catalog.summarise(ALL_EVENTS["GW150914-v3"], intervals=True)
    assert summary["total_mass_source"] == {"value": 63.1, "interval_90": [60.1, 66.4]}
    assert summary["luminosity_distance"] == {"value": 440.0, "interval_90": None}
    assert summary["redshift"] is None


def test_get_catalog_event_by_name(gwosc):
    result = server.get_catalog_event("GW190814")
    assert result["gracedb_id"] == "S190814bv"
    assert result["mass_1_source"]["interval_90"] == [22.2, 24.3]
    assert result["strain_files"] == [
        {"detector": "H1", "duration_s": 32, "sample_rate": 16384},
        {"detector": "L1", "duration_s": 4096, "sample_rate": 4096},
    ]
    assert result["parameter_sets"] == ["pe_a", "pe_b"]
    assert result["gwosc_page"].endswith("/GWTC-2.1-confident/GW190814/v2/")


def test_get_catalog_event_by_superevent_id(gwosc, fake_client):
    assert server.get_catalog_event("S190814bv")["name"] == "GW190814"


def test_get_catalog_event_unpublished_superevent(gwosc, fake_client):
    result = server.get_catalog_event("S230518h")
    assert "not in any GWOSC catalogue" in result["error"]


def test_get_catalog_event_unknown_name_suggests(gwosc):
    result = server.get_catalog_event("GW200105")
    assert result["similar"] == ["GW200105_162426"]


def test_get_catalog_event_rejects_unsafe_name(gwosc):
    with pytest.raises(ValueError):
        server.get_catalog_event("../GW150914")


def test_get_event_labels(fake_client):
    result = server.get_event_labels("S190814bv")
    assert [label["name"] for label in result["labels"]] == ["DQOK", "PE_READY", "MYSTERY"]
    assert result["labels"][0]["meaning"].startswith("Automated data-quality")
    assert result["labels"][2]["meaning"] is None
    assert all("creator" not in label for label in result["labels"])
    assert result["retracted"] is False
