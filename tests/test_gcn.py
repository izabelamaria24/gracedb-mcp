"""Tests for the GCN Circulars tools (GCN responses stubbed, no network)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest
from test_catalog import gwosc  # noqa: F401 - pytest fixture (GW190814 -> S190814bv)

from gracedb_mcp import gcn_client
from gracedb_mcp.tools import gcn

CIRCULARS = {
    33813: {"circularId": 33813, "subject": "LIGO/Virgo/KAGRA S230518h: Identification",
            "eventId": "LIGO/Virgo/KAGRA S230518h", "createdOn": 1684418785133,
            "bibcode": "2023GCN.33813....1L", "submitter": "someone@example.org",
            "body": "We identified S230518h."},
    33818: {"circularId": 33818, "subject": "LIGO/Virgo/KAGRA S230518h: Fermi-GBM limits",
            "eventId": "LIGO/Virgo/KAGRA S230518h", "createdOn": 1684442197502,
            "submitter": "gbm@example.org", "body": "Fermi-GBM upper limits."},
}


@pytest.fixture
def gcn_api(monkeypatch):
    """Stub GCN: records each request; search returns CIRCULARS, newest first."""
    cached = gcn_client.circular
    cached.cache_clear()
    calls: list[dict] = []

    def get_json(url):
        parsed = urllib.parse.urlparse(url)
        if parsed.path.endswith(".json"):
            cid = int(parsed.path.rsplit("/", 1)[1].removesuffix(".json"))
            calls.append({"circular": cid})
            if cid not in CIRCULARS:
                raise gcn_client.GcnError("not found")
            return CIRCULARS[cid]
        params = dict(urllib.parse.parse_qsl(parsed.query))
        calls.append(params)
        items = [{"circularId": str(c), "subject": CIRCULARS[c]["subject"]}
                 for c in sorted(CIRCULARS, reverse=True)]
        limit = int(params["limit"])
        return {"items": items[:limit], "totalItems": len(items), "totalPages": 1,
                "queryFallback": params.get("query") == "plain words"}

    monkeypatch.setattr(gcn_client, "_get_json", get_json)
    yield calls
    cached.cache_clear()


def test_search_by_event(gcn_api):
    result = gcn.search_gcn_circulars(event="S230518h")
    search = gcn_api[0]
    assert search["query"] == 'subject:"S230518h"'
    assert search["_data"] == "routes/circulars._archive._index" and search["page"] == "1"
    assert "sort" not in search
    assert result["total"] == 2 and [c["id"] for c in result["circulars"]] == [33818, 33813]
    first = result["circulars"][1]
    assert first == {
        "id": 33813,
        "subject": "LIGO/Virgo/KAGRA S230518h: Identification",
        "event": "LIGO/Virgo/KAGRA S230518h",
        "created": "2023-05-18 14:06:25 UTC",
        "url": "https://gcn.nasa.gov/circulars/33813",
        "ads": "https://ui.adsabs.harvard.edu/abs/2023GCN.33813....1L",
    }
    assert result["circulars"][0]["ads"] is None  # no bibcode yet
    assert "names_searched" not in result and "note" not in result
    assert result["archive_url"] == "https://gcn.nasa.gov/circulars?query=subject%3A%22S230518h%22"


def test_never_returns_submitter_email(gcn_api):
    result = gcn.search_gcn_circulars(event="S230518h", include_bodies=True)
    assert result["circulars"][1]["body"] == "We identified S230518h."
    assert "example.org" not in json.dumps(result)
    assert "example.org" not in json.dumps(gcn.get_gcn_circular(33813))


def test_gw_name_also_searches_its_gracedb_id(gcn_api, gwosc):  # noqa: F811
    result = gcn.search_gcn_circulars(event="GW190814")
    assert gcn_api[0]["query"] == 'subject:"GW190814" OR subject:"S190814bv"'
    assert result["names_searched"] == ["GW190814", "S190814bv"]


def test_unknown_gw_name_is_searched_as_is(gcn_api, gwosc):  # noqa: F811
    gcn.search_gcn_circulars(event="GW999999")
    assert gcn_api[0]["query"] == 'subject:"GW999999"'


def test_event_query_dates_sort_and_page(gcn_api):
    result = gcn.search_gcn_circulars(
        event="S230518h", query='subject:"Fermi"', start_date="2023-05-18",
        end_date="2023-05-20", sort="relevance", page=2, max_results=1,
    )
    search = gcn_api[0]
    assert search["query"] == '(subject:"S230518h") AND (subject:"Fermi")'
    assert (search["startDate"], search["endDate"]) == ("2023-05-18", "2023-05-20")
    assert (search["sort"], search["page"], search["limit"]) == ("relevance", "2", "1")
    assert result["page"] == 2 and len(result["circulars"]) == 1
    assert "sort=relevance" in result["archive_url"] and "startDate=2023-05-18" in result["archive_url"]


def test_max_results_is_capped(gcn_api):
    gcn.search_gcn_circulars(event="S230518h", max_results=500)
    assert gcn_api[0]["limit"] == str(gcn.MAX_RESULTS)


def test_query_fallback_is_reported(gcn_api):
    assert "plain-text" in gcn.search_gcn_circulars(query="plain words")["note"]


def test_failed_circular_fetch_is_reported_per_item(gcn_api, monkeypatch):
    def circular(cid):
        if cid == 33818:
            raise gcn_client.GcnError("GCN returned HTTP 503")
        return CIRCULARS[cid]

    monkeypatch.setattr(gcn_client, "circular", circular)
    result = gcn.search_gcn_circulars(query="anything")
    assert result["circulars"][0] == {"id": 33818, "error": "GCN returned HTTP 503"}
    assert result["circulars"][1]["id"] == 33813 and "error" not in result["circulars"][1]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "give an event"),
        ({"event": 'x" OR "y'}, "invalid event name"),
        ({"event": "S1", "start_date": "18/05/2023"}, "YYYY-MM-DD"),
        ({"event": "S1", "sort": "oldest"}, "sort must be"),
    ],
)
def test_search_rejects_bad_input(gcn_api, kwargs, message):
    assert message in gcn.search_gcn_circulars(**kwargs)["error"]
    assert gcn_api == []


def test_get_gcn_circular(gcn_api):
    assert gcn.get_gcn_circular(33818)["body"] == "Fermi-GBM upper limits."
    gcn.get_gcn_circular(33818)
    assert gcn_api.count({"circular": 33818}) == 1  # cached
    assert "not found" in gcn.get_gcn_circular(1)["error"]
    assert "positive" in gcn.get_gcn_circular(0)["error"]


# -- HTTP layer ----------------------------------------------------------


class _Response(io.BytesIO):
    def __init__(self, body: bytes, content_type: str):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_json_sends_user_agent_and_parses(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["ua"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return _Response(b'{"ok": 1}', "application/json; charset=utf-8")

    monkeypatch.setattr(gcn_client.urllib.request, "urlopen", urlopen)
    assert gcn_client._get_json("https://gcn.nasa.gov/x") == {"ok": 1}
    assert seen["ua"].startswith("gracedb-mcp/") and seen["timeout"] == gcn_client.TIMEOUT


def test_get_json_rejects_html(monkeypatch):
    monkeypatch.setattr(
        gcn_client.urllib.request, "urlopen", lambda r, timeout: _Response(b"<html>", "text/html")
    )
    with pytest.raises(gcn_client.GcnError, match="HTML"):
        gcn_client._get_json("https://gcn.nasa.gov/x")


@pytest.mark.parametrize(("code", "message"), [(404, "not found"), (503, "HTTP 503")])
def test_get_json_http_errors(monkeypatch, code, message):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "err", {}, None)

    monkeypatch.setattr(gcn_client.urllib.request, "urlopen", urlopen)
    with pytest.raises(gcn_client.GcnError, match=message):
        gcn_client._get_json("https://gcn.nasa.gov/x")


def test_search_explains_server_errors_as_bad_syntax(monkeypatch):
    def get_json(url):
        raise gcn_client.GcnError("GCN returned HTTP 500")

    monkeypatch.setattr(gcn_client, "_get_json", get_json)
    with pytest.raises(gcn_client.GcnError, match="Lucene syntax"):
        gcn_client.search('subject:"Swift" AND')
    with pytest.raises(gcn_client.GcnError, match="HTTP 500"):
        gcn_client.search(None, start_date="2023-01-01")


def test_search_rejects_unexpected_shape(monkeypatch):
    monkeypatch.setattr(gcn_client, "_get_json", lambda url: ["not", "a", "dict"])
    with pytest.raises(gcn_client.GcnError, match="unexpected"):
        gcn_client.search("x")
