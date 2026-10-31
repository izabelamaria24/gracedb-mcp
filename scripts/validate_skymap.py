"""Compare gracedb_mcp.skymap (pure Python) with ligo.skymap / astropy-healpix on real maps.

Run in an isolated environment that has ligo.skymap, e.g.:
    uv run --with ligo.skymap --with-editable . python scripts/validate_skymap.py /tmp/skyval/*.fits
"""

from __future__ import annotations

import random
import sys

import astropy.units as u
import numpy as np
from astropy_healpix import HEALPix
from ligo.skymap.distance import parameters_to_moments
from ligo.skymap.io import read_sky_map
from ligo.skymap.moc import uniq2nest, uniq2pixarea

from gracedb_mcp import skymap as ours


def reference(path: str):
    table = read_sky_map(path, moc=True)
    uniq = np.asarray(table["UNIQ"])
    density = np.asarray(table["PROBDENSITY"])
    area = uniq2pixarea(uniq)
    order = np.argsort(-density)
    cum_prob = np.cumsum((density * area)[order])
    cum_area = np.cumsum(area[order])
    return table, uniq, density, area, order, cum_prob, cum_area


def main(paths: list[str]) -> None:
    random.seed(0)
    worst = 0.0
    for path in paths:
        with open(path, "rb") as f:
            mine = ours.SkyMap.from_bytes(f.read())
        table, uniq, density, area, order, cum_prob, cum_area = reference(path)
        name = path.rsplit("/", 1)[-1]
        assert len(mine.pixels) == len(uniq), "row count"
        assert abs(mine.total_prob - cum_prob[-1]) < 1e-9, "total probability"

        for level in (0.5, 0.9):
            ref_area = np.interp(level * cum_prob[-1], cum_prob, cum_area) * (180 / np.pi) ** 2
            got = mine.credible_area_deg2(level)
            rel = abs(got - ref_area) / ref_area
            worst = max(worst, rel)
            print(f"{name}: {int(level * 100)}% area ours {got:9.2f} ref {ref_area:9.2f} rel {rel:.1e}")

        # Peak pixel centre via astropy-healpix.
        top = order[0]
        level_, ipix = uniq2nest(uniq[top])  # (HEALPix order, nested index)
        hp = HEALPix(nside=1 << int(level_), order="nested")
        lon, lat = hp.healpix_to_lonlat(int(ipix))
        ra, dec = mine.peak()
        print(f"   peak ours ({ra:.4f}, {dec:.4f}) ref ({lon.deg:.4f}, {lat.deg:.4f})")
        assert abs(ra - lon.deg) < 1e-6 and abs(dec - lat.deg) < 1e-6

        # Point queries: peak, a pixel ~20% down the ranking, and a random point.
        probes = [(ra, dec)]
        idx = order[len(order) // 5]
        o2, p2 = uniq2nest(uniq[idx])
        lon2, lat2 = HEALPix(nside=1 << int(o2), order="nested").healpix_to_lonlat(int(p2))
        probes += [(lon2.deg, lat2.deg), (random.uniform(0, 360), random.uniform(-80, 80))]
        orders_present = sorted({int(o) for o in uniq2nest(uniq)[0]})
        for pra, pdec in probes:
            got = mine.at(pra, pdec)
            hit = None
            for o in orders_present:
                ns = 1 << o
                pix = HEALPix(nside=ns, order="nested").lonlat_to_healpix(pra * u.deg, pdec * u.deg)
                match = np.nonzero(uniq == 4 * ns * ns + int(pix))[0]
                if len(match):
                    hit = match[0]
                    break
            rank = int(np.nonzero(order == hit)[0][0])
            ref_level = cum_prob[rank] / cum_prob[-1]
            mean, std, _ = parameters_to_moments(table["DISTMU"][hit], table["DISTSIGMA"][hit])
            assert abs(got["credible_level"] - ref_level) < 1e-9, (got, ref_level)
            assert abs(got["prob_density_per_deg2"] - density[hit] / (180 / np.pi) ** 2) < 1e-15
            if got["distance_mpc"] is not None and np.isfinite(mean):
                assert abs(got["distance_mpc"]["mean"] - mean) / mean < 1e-6, (got, mean)
                assert abs(got["distance_mpc"]["std"] - std) / std < 1e-6, (got, std)
            print(f"   at ({pra:7.2f}, {pdec:6.2f}) level ours {got['credible_level']:.6f} "
                  f"ref {ref_level:.6f} dist {got['distance_mpc']} ref ({float(mean):.1f}, {float(std):.1f})")

        # Cone probability vs a brute-force numpy computation on pixel centres.
        nest = uniq2nest(uniq)
        centres = [
            HEALPix(nside=1 << int(o), order="nested").healpix_to_lonlat(int(p)) for o, p in zip(*nest)
        ]
        cra = np.array([c[0].deg for c in centres])
        cdec = np.array([c[1].deg for c in centres])
        sep = np.degrees(np.arccos(np.clip(
            np.sin(np.radians(dec)) * np.sin(np.radians(cdec))
            + np.cos(np.radians(dec)) * np.cos(np.radians(cdec)) * np.cos(np.radians(cra - ra)), -1, 1)))
        ref_cone = float(np.sum((density * area)[sep <= 2.0]) / cum_prob[-1])
        got_cone = mine.prob_within(ra, dec, 2.0)
        print(f"   2 deg cone at peak ours {got_cone:.6f} ref {ref_cone:.6f}")
        assert abs(got_cone - ref_cone) < 1e-6

    # HEALPix conversions on random points at several resolutions.
    for order_ in (0, 3, 7, 11, 13):
        hp = HEALPix(nside=1 << order_, order="nested")
        for _ in range(2000):
            ra, dec = random.uniform(0, 360), random.uniform(-90, 90)
            assert ours.ang2pix_nest(1 << order_, ra, dec) == int(hp.lonlat_to_healpix(ra * u.deg, dec * u.deg))
            pix = random.randrange(hp.npix)
            lon, lat = hp.healpix_to_lonlat(pix)
            pra, pdec = ours.pix2ang_nest(1 << order_, pix)
            assert ours.angular_distance_deg(pra, pdec, lon.deg, lat.deg) < 1e-7
    print(f"HEALPix conversions match on 10000 random points; worst credible-area rel diff {worst:.1e}")


if __name__ == "__main__":
    main(sys.argv[1:])
