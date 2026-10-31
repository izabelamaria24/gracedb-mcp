"""Tests for the pure-Python sky-map code and the analyze_skymap tool (no network).

Reference values come from astropy-healpix and ligo.skymap; scripts/validate_skymap.py
compares the two implementations on real LVK sky maps.
"""

from __future__ import annotations

import gzip
import math
import struct

import pytest
from test_tools import FakeGraceDbClient

from gracedb_mcp import app, skymap
from gracedb_mcp.tools import skymaps

DEG2 = (180 / math.pi) ** 2
AREA0 = 4 * math.pi / 12  # sr per order-0 pixel
AREA1 = AREA0 / 4

# (uniq, probability, distmu, distsigma): order-0 pixel 0 is split into four order-1
# pixels (uniq 16-19); the other eleven order-0 pixels share half the probability.
ROWS = [
    (16, 0.4, 100.0, 10.0),
    (17, 0.1, 250.0, 80.0),
    (18, 0.0, math.inf, 1.0),
    (19, 0.0, 50.0, 10.0),  # no probability, but a finite distance fit
    *((4 + i, 0.5 / 11, 30.0, 60.0) for i in range(1, 12)),
]


def _card(key: str, value) -> bytes:
    if isinstance(value, str):
        text = f"{key:<8}= '{value:<8}' / a comment with / slashes"
    elif isinstance(value, bool):
        text = f"{key:<8}= {'T' if value else 'F':>20}"
    else:
        text = f"{key:<8}= {value!r:>20} / comment"
    return text[:80].ljust(80).encode()


def _hdu_header(cards: list[tuple[str, object]]) -> bytes:
    raw = b"".join(_card(k, v) for k, v in cards) + b"END".ljust(80)
    return raw.ljust(-(-len(raw) // 2880) * 2880, b" ")


def make_skymap(rows=ROWS, **extra) -> bytes:
    primary = _hdu_header([("SIMPLE", True), ("BITPIX", 8), ("NAXIS", 0), ("EXTEND", True)])
    columns = [("UNIQ", "K"), ("PROBDENSITY", "D"), ("DISTMU", "D"), ("DISTSIGMA", "D"),
               ("DISTNORM", "D")]
    cards: list[tuple[str, object]] = [
        ("XTENSION", "BINTABLE"), ("BITPIX", 8), ("NAXIS", 2), ("NAXIS1", 40),
        ("NAXIS2", len(rows)), ("PCOUNT", 0), ("GCOUNT", 1), ("TFIELDS", len(columns)),
    ]
    for i, (name, form) in enumerate(columns, 1):
        cards += [(f"TTYPE{i}", name), (f"TFORM{i}", form)]
    cards += [("ORDERING", "NUNIQ"), *extra.items()]
    data = b""
    for uniq, prob, mu, sigma in rows:
        area = AREA1 if uniq >= 16 else AREA0
        data += struct.pack(">qdddd", uniq, prob / area, mu, sigma, 1.0)
    data = data.ljust(-(-len(data) // 2880) * 2880, b"\0")
    return primary + _hdu_header(cards) + data


# -- HEALPix, checked against astropy-healpix ----------------------------


@pytest.mark.parametrize(
    ("nside", "ra", "dec", "ipix"),
    [
        (1, 45.0, 30.0, 0),
        (1, 200.0, -60.0, 10),
        (16, 12.7, -24.87, 1041),
        (1024, 96.59, -14.02, 5368712),
        (8192, 359.99, 89.9, 268434941),
        (4, 180.3, 0.2, 102),  # interior point; exact pixel corners are ambiguous
    ],
)
def test_ang2pix_nest(nside, ra, dec, ipix):
    assert skymap.ang2pix_nest(nside, ra, dec) == ipix


@pytest.mark.parametrize(
    ("nside", "ipix", "ra", "dec"),
    [
        (1, 0, 45.0, 41.810314895778596),
        (1, 5, 90.0, 0.0),
        (1, 11, 315.0, -41.810314895778596),
        (64, 12345, 314.296875, 7.180755781458282),
        (2048, 40000000, 101.3905325443787, -54.70065816700276),
    ],
)
def test_pix2ang_nest(nside, ipix, ra, dec):
    got_ra, got_dec = skymap.pix2ang_nest(nside, ipix)
    assert got_ra == pytest.approx(ra, abs=1e-9) and got_dec == pytest.approx(dec, abs=1e-9)


def test_round_trip_and_uniq():
    for nside in (1, 2, 64, 1024):
        for ipix in (0, 7, 12 * nside * nside - 1, 5 * nside * nside + 3):
            ra, dec = skymap.pix2ang_nest(nside, ipix)
            assert skymap.ang2pix_nest(nside, ra, dec) == ipix
    assert skymap.uniq_to_order_ipix(4) == (0, 0)
    assert skymap.uniq_to_order_ipix(19) == (1, 3)
    assert skymap.uniq_to_order_ipix(4 * 1024**2 + 99) == (10, 99)


@pytest.mark.parametrize(
    ("mu", "sigma", "mean", "std"),
    [  # ligo.skymap.distance.parameters_to_moments
        (100.0, 10.0, 101.98019801980197, 9.902475136155353),
        (250.0, 80.0, 296.44829424077176, 73.69180688468519),
        (30.0, 60.0, 110.48706921619876, 43.67172554984363),
        (-20.0, 50.0, 71.29807858289249, 31.474154773874975),
    ],
)
def test_conditional_distance(mu, sigma, mean, std):
    got = skymap.conditional_distance(mu, sigma)
    assert got[0] == pytest.approx(mean, rel=1e-9) and got[1] == pytest.approx(std, rel=1e-9)


def test_conditional_distance_undefined():
    assert skymap.conditional_distance(math.inf, 1.0) is None
    assert skymap.conditional_distance(100.0, 0.0) is None


# -- Sky map -------------------------------------------------------------


@pytest.mark.parametrize("compress", [False, True])
def test_skymap_reads_header_and_credible_areas(compress):
    raw = make_skymap(DISTMEAN=120.5, DISTSTD=30.25, INSTRUME="H1,L1", CREATOR="BAYESTAR")
    sky = skymap.SkyMap.from_bytes(gzip.compress(raw) if compress else raw)
    assert sky.header["INSTRUME"] == "H1,L1" and sky.header["DISTMEAN"] == 120.5
    assert sky.total_prob == pytest.approx(1.0)
    assert sky.credible_area_deg2(0.4) == pytest.approx(AREA1 * DEG2)
    assert sky.credible_area_deg2(0.5) == pytest.approx(2 * AREA1 * DEG2)
    assert sky.credible_area_deg2(0.2) == pytest.approx(0.5 * AREA1 * DEG2)  # interpolated
    assert sky.peak() == pytest.approx(skymap.pix2ang_nest(2, 0))


def test_skymap_point_queries():
    sky = skymap.SkyMap.from_bytes(make_skymap())
    best = sky.at(*skymap.pix2ang_nest(2, 0))
    assert best["credible_level"] == pytest.approx(0.4)
    assert best["searched_area_deg2"] == pytest.approx(AREA1 * DEG2)
    assert best["prob_density_per_deg2"] == pytest.approx(0.4 / AREA1 / DEG2)
    assert best["distance_mpc"]["mean"] == pytest.approx(101.98019801980197)
    second = sky.at(*skymap.pix2ang_nest(2, 1))
    assert second["credible_level"] == pytest.approx(0.5)
    low = sky.at(*skymap.pix2ang_nest(1, 6))
    assert 0.5 < low["credible_level"] <= 1.0


def test_prob_within_cone():
    sky = skymap.SkyMap.from_bytes(make_skymap())
    assert sky.prob_within(0, 0, 180) == pytest.approx(1.0)  # whole sky
    ra, dec = skymap.pix2ang_nest(2, 0)
    assert sky.prob_within(ra, dec, 1.0) == pytest.approx(0.4)  # only that pixel's centre


def test_rejects_unsupported_maps():
    flat = make_skymap().replace(b"ORDERING= 'NUNIQ   '", b"ORDERING= 'NESTED  '")
    with pytest.raises(ValueError, match="multi-order"):
        skymap.SkyMap.from_bytes(flat)
    with pytest.raises(ValueError, match="truncated"):
        skymap.read_fits_table(b"SIMPLE")


# -- analyze_skymap tool -------------------------------------------------


@pytest.fixture
def skymap_client(monkeypatch, tmp_path):
    client = FakeGraceDbClient()
    files = {
        "bayestar.multiorder.fits": make_skymap(DISTMEAN=120.0, DISTSTD=30.0, INSTRUME="H1,L1",
                                                CREATOR="BAYESTAR"),
        "Bilby.multiorder.fits": make_skymap(DISTMEAN=110.0, DISTSTD=20.0, CREATOR="Bilby"),
    }
    client.list_files = lambda sid: {**dict.fromkeys(files, "url"), "p_astro.json": "url"}
    client.get_logs = lambda sid: [
        {"filename": "bayestar.multiorder.fits", "created": "2023-05-18 13:26:10 UTC"},
        {"filename": "bayestar.multiorder.fits", "created": "2023-05-18 14:01:14 UTC"},
        {"filename": "Bilby.multiorder.fits", "created": "2023-05-18 15:33:30 UTC"},
        {"filename": "", "created": "2023-05-18 16:00:00 UTC"},
    ]

    def download(sid, name, dest):
        with open(dest, "wb") as handle:
            handle.write(files[name])
        return dest

    client.download_file = download
    monkeypatch.setattr(app, "client", client)
    monkeypatch.setattr(app, "DATA_DIR", str(tmp_path))
    return client


def test_analyze_skymap_defaults_to_newest_map(skymap_client):
    result = skymaps.analyze_skymap("S230518h")
    assert result["skymap"] == "Bilby.multiorder.fits" and result["created_by"] == "Bilby"
    assert result["uploaded"] == "2023-05-18 15:33:30 UTC"
    assert [m["filename"] for m in result["available_skymaps"]] == [
        "Bilby.multiorder.fits", "bayestar.multiorder.fits"
    ]
    assert result["available_skymaps"][1]["uploaded"] == "2023-05-18 14:01:14 UTC"
    assert result["distance_mpc"] == {"mean": 110.0, "std": 20.0}
    assert result["area_50_deg2"] == pytest.approx(2 * AREA1 * DEG2, rel=1e-3)
    assert result["resource"] == "gracedb://files/S230518h/Bilby.multiorder.fits"


def test_analyze_skymap_positions_and_fields(skymap_client):
    ra, dec = skymap.pix2ang_nest(2, 0)
    far_ra, far_dec = skymap.pix2ang_nest(2, 3)
    result = skymaps.analyze_skymap(
        "S230518h",
        filename="bayestar.multiorder.fits",
        positions=[skymaps.SkyPosition(ra=ra, dec=dec, name="host?"),
                   skymaps.SkyPosition(ra=far_ra, dec=far_dec)],
        fields=[skymaps.TelescopeField(ra=ra, dec=dec, radius=1.0, name="tile 1")],
    )
    assert result["detectors"] == ["H1", "L1"]
    near, far = result["positions"]
    assert near["name"] == "host?" and near["inside_90"] and near["credible_level"] == 0.4
    assert near["distance_mpc"] == {"mean": 102.0, "std": 9.902}
    assert far["credible_level"] > 0.99 and far["distance_mpc"] is None
    assert "name" not in far
    assert result["fields"] == [
        {"ra": ra, "dec": dec, "name": "tile 1", "radius": 1.0, "probability": 0.4}
    ]


def test_analyze_skymap_errors(skymap_client):
    assert "available_skymaps" in skymaps.analyze_skymap("S230518h", filename="p_astro.json")
    too_many = [skymaps.SkyPosition(ra=0, dec=0)] * (skymaps.MAX_POSITIONS + 1)
    assert "at most" in skymaps.analyze_skymap("S230518h", positions=too_many)["error"]
    skymap_client.list_files = lambda sid: {"p_astro.json": "url"}
    assert "no multi-order sky map" in skymaps.analyze_skymap("S230518h")["error"]
    with pytest.raises(ValueError):
        skymaps.analyze_skymap("../etc")
    with pytest.raises(ValueError):
        skymaps.SkyPosition(ra=360, dec=0)
