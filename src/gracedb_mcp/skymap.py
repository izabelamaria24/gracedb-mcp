"""Multi-order (NUNIQ) HEALPix sky maps in pure Python: no numpy, astropy or healpy.

LVK sky maps are a FITS binary table of HEALPix pixels at mixed resolutions. Each row
has a UNIQ pixel index, a probability density per steradian, and the parameters of
the per-direction distance distribution (DISTMU, DISTSIGMA, DISTNORM). The HEALPix
conversions follow the reference C implementation (Gorski et al. 2005).
"""

from __future__ import annotations

import bisect
import gzip
import math
import struct
from dataclasses import dataclass
from typing import Any

_BLOCK = 2880
_FORMATS = {"K": "q", "J": "i", "I": "h", "D": "d", "E": "f", "B": "B"}
_JRLL = (2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4)
_JPLL = (1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7)
DEG2_PER_SR = (180 / math.pi) ** 2


# -- FITS ----------------------------------------------------------------


def _read_header(data: bytes, offset: int) -> tuple[dict[str, Any], int]:
    header: dict[str, Any] = {}
    while True:
        block = data[offset:offset + _BLOCK]
        if len(block) < _BLOCK:
            raise ValueError("truncated FITS header")
        offset += _BLOCK
        for i in range(0, _BLOCK, 80):
            card = block[i:i + 80].decode("ascii", "replace")
            key = card[:8].strip()
            if key == "END":
                return header, offset
            if card[8:10] != "= ":
                continue
            raw = card[10:].strip()
            if raw.startswith("'"):
                header[key] = raw[1:].split("'", 1)[0].rstrip()
                continue
            value = raw.split("/", 1)[0].strip()
            if value in ("T", "F"):
                header[key] = value == "T"
            else:
                try:
                    header[key] = int(value)
                except ValueError:
                    try:
                        header[key] = float(value)
                    except ValueError:
                        header[key] = value


def read_fits_table(raw: bytes) -> tuple[dict[str, Any], dict[str, list]]:
    """Header and columns of the first binary-table extension of a (gzipped) FITS file."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    primary, offset = _read_header(raw, 0)
    offset += _data_size(primary)
    header, offset = _read_header(raw, offset)
    if header.get("XTENSION") != "BINTABLE":
        raise ValueError("expected a FITS binary table")
    names, fmt = [], ">"
    for i in range(1, header["TFIELDS"] + 1):
        form = header[f"TFORM{i}"].strip()
        repeat, code = int(form[:-1] or 1), form[-1]
        if repeat != 1 or code not in _FORMATS:
            raise ValueError(f"unsupported column format {form!r}")
        names.append(header[f"TTYPE{i}"])
        fmt += _FORMATS[code]
    width, rows = header["NAXIS1"], header["NAXIS2"]
    if struct.calcsize(fmt) != width:
        raise ValueError("column formats do not match the row width")
    values = struct.iter_unpack(fmt, raw[offset:offset + width * rows])
    columns = dict(zip(names, (list(col) for col in zip(*values))))
    return header, columns


def _data_size(header: dict[str, Any]) -> int:
    if not header.get("NAXIS"):
        return 0
    size = abs(header["BITPIX"]) // 8
    for i in range(1, header["NAXIS"] + 1):
        size *= header[f"NAXIS{i}"]
    return -(-size // _BLOCK) * _BLOCK


# -- HEALPix (nested scheme) ---------------------------------------------


def _spread(v: int) -> int:
    """Interleave zeros between the bits of v (bit i -> bit 2i)."""
    out, shift = 0, 0
    while v:
        out |= (v & 1) << shift
        v >>= 1
        shift += 2
    return out


def _compress(v: int) -> int:
    """Inverse of _spread on the even bits of v."""
    out, shift = 0, 0
    while v:
        out |= (v & 1) << shift
        v >>= 2
        shift += 1
    return out


def ang2pix_nest(nside: int, ra_deg: float, dec_deg: float) -> int:
    theta = math.radians(90.0 - dec_deg)
    z, s = math.cos(theta), math.sin(theta)
    za = abs(z)
    tt = (math.radians(ra_deg) % (2 * math.pi)) / (math.pi / 2)  # in [0, 4)
    if za <= 2 / 3:  # equatorial region
        temp1, temp2 = nside * (0.5 + tt), nside * z * 0.75
        jp, jm = int(temp1 - temp2), int(temp1 + temp2)
        ifp, ifm = jp // nside, jm // nside
        face = (ifp | 4) if ifp == ifm else (ifp if ifp < ifm else ifm + 8)
        ix = jm & (nside - 1)
        iy = nside - (jp & (nside - 1)) - 1
    else:  # polar caps
        ntt = min(int(tt), 3)
        tp = tt - ntt
        tmp = nside * math.sqrt(3 * (1 - za)) if za < 0.99 else nside * s / math.sqrt((1 + za) / 3)
        jp, jm = min(int(tp * tmp), nside - 1), min(int((1 - tp) * tmp), nside - 1)
        if z >= 0:
            face, ix, iy = ntt, nside - jm - 1, nside - jp - 1
        else:
            face, ix, iy = ntt + 8, jp, jm
    return face * nside * nside + _spread(ix) + (_spread(iy) << 1)


def pix2ang_nest(nside: int, ipix: int) -> tuple[float, float]:
    """Centre of a nested pixel as (ra, dec) in degrees."""
    npface = nside * nside
    face, within = divmod(ipix, npface)
    ix, iy = _compress(within), _compress(within >> 1)
    jr = _JRLL[face] * nside - ix - iy - 1
    if jr < nside:
        nr, z, kshift = jr, 1 - jr * jr / (3 * npface), 0
    elif jr > 3 * nside:
        nr = 4 * nside - jr
        z, kshift = nr * nr / (3 * npface) - 1, 0
    else:
        nr, z, kshift = nside, (2 * nside - jr) * 2 / (3 * nside), (jr - nside) & 1
    jp = (_JPLL[face] * nr + ix - iy + 1 + kshift) // 2
    if jp > 4 * nr:
        jp -= 4 * nr
    if jp < 1:
        jp += 4 * nr
    phi = (jp - (kshift + 1) * 0.5) * (math.pi / 2 / nr)
    return math.degrees(phi), 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, z))))


def uniq_to_order_ipix(uniq: int) -> tuple[int, int]:
    order = ((uniq >> 2).bit_length() - 1) // 2
    return order, uniq - 4 * (1 << (2 * order))


def angular_distance_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1, dec1, ra2, dec2))
    # Haversine form: accurate for small separations.
    a = math.sin((dec2 - dec1) / 2) ** 2 + math.cos(dec1) * math.cos(dec2) * math.sin((ra2 - ra1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


# -- Distance ------------------------------------------------------------


def conditional_distance(mu: float, sigma: float) -> tuple[float, float] | None:
    """Mean and standard deviation of p(r) ~ r^2 N(r; mu, sigma) for r > 0.

    This is the per-direction distance distribution of LVK sky maps (Singer et al. 2016).
    """
    if not (math.isfinite(mu) and math.isfinite(sigma)) or sigma <= 0:
        return None
    # Closed-form moments of r^k N(r; mu, sigma) over r > 0, via the standard normal.
    t = mu / sigma
    phi = math.exp(-t * t / 2) / math.sqrt(2 * math.pi)
    cdf = 0.5 * math.erfc(-t / math.sqrt(2))
    # m_k = E[x^k; x > -t] for standard normal x, then expand (mu + sigma x)^n.
    m = [cdf, phi, cdf - t * phi]
    m.append(2 * phi + t * t * phi)
    m.append(3 * m[2] - t ** 3 * phi)

    def moment(n: int) -> float:
        return sum(math.comb(n, k) * mu ** (n - k) * sigma ** k * m[k] for k in range(n + 1))

    norm = moment(2)
    if norm <= 0:
        return None
    mean = moment(3) / norm
    variance = moment(4) / norm - mean * mean
    return mean, math.sqrt(max(variance, 0.0))


# -- Sky map -------------------------------------------------------------


@dataclass
class _Pixel:
    order: int
    ipix: int
    density: float  # per steradian
    area: float  # steradians
    distmu: float
    distsigma: float


class SkyMap:
    """A multi-order sky map, with pixels sorted from most to least probable."""

    def __init__(self, header: dict[str, Any], columns: dict[str, list]) -> None:
        if header.get("ORDERING") != "NUNIQ":
            raise ValueError("only multi-order (NUNIQ) sky maps are supported")
        self.header = header
        n = len(columns["UNIQ"])
        distmu = columns.get("DISTMU", [math.inf] * n)
        distsigma = columns.get("DISTSIGMA", [math.inf] * n)
        pixels = []
        for uniq, density, mu, sigma in zip(columns["UNIQ"], columns["PROBDENSITY"], distmu, distsigma):
            order, ipix = uniq_to_order_ipix(uniq)
            area = 4 * math.pi / (12 * (1 << (2 * order)))
            pixels.append(_Pixel(order, ipix, density, area, mu, sigma))
        pixels.sort(key=lambda p: p.density, reverse=True)
        self.pixels = pixels
        self.cum_prob, self.cum_area = [], []
        prob = area = 0.0
        for p in pixels:
            prob += p.density * p.area
            area += p.area
            self.cum_prob.append(prob)
            self.cum_area.append(area)
        self.total_prob = prob
        self._rank = {(p.order, p.ipix): i for i, p in enumerate(pixels)}
        self._orders = sorted({p.order for p in pixels})
        self._centres: list[tuple[float, float]] | None = None

    @classmethod
    def from_bytes(cls, raw: bytes) -> SkyMap:
        return cls(*read_fits_table(raw))

    def credible_area_deg2(self, level: float) -> float:
        """Area of the smallest region holding ``level`` of the probability."""
        target = level * self.total_prob
        i = bisect.bisect_left(self.cum_prob, target)
        if i == 0:
            return self.cum_area[0] * target / self.cum_prob[0] * DEG2_PER_SR
        if i >= len(self.cum_prob):
            return self.cum_area[-1] * DEG2_PER_SR
        p0, p1 = self.cum_prob[i - 1], self.cum_prob[i]
        a0, a1 = self.cum_area[i - 1], self.cum_area[i]
        return (a0 + (a1 - a0) * (target - p0) / (p1 - p0)) * DEG2_PER_SR

    def peak(self) -> tuple[float, float]:
        p = self.pixels[0]
        return pix2ang_nest(1 << p.order, p.ipix)

    def _locate(self, ra: float, dec: float) -> int | None:
        for order in self._orders:
            rank = self._rank.get((order, ang2pix_nest(1 << order, ra, dec)))
            if rank is not None:
                return rank
        return None

    def at(self, ra: float, dec: float) -> dict[str, Any]:
        """Probability density, credible level and distance at a sky position."""
        rank = self._locate(ra, dec)
        if rank is None:
            return {"error": "position not covered by the sky map"}
        p = self.pixels[rank]
        distance = conditional_distance(p.distmu, p.distsigma)
        return {
            "prob_density_per_deg2": p.density / DEG2_PER_SR,
            # Fraction of probability in pixels at least as likely: the smallest credible
            # region containing this position.
            "credible_level": min(1.0, self.cum_prob[rank] / self.total_prob),
            "searched_area_deg2": self.cum_area[rank] * DEG2_PER_SR,
            "distance_mpc": None if distance is None
            else {"mean": distance[0], "std": distance[1]},
        }

    def prob_within(self, ra: float, dec: float, radius_deg: float) -> float:
        """Probability in a circle (e.g. a telescope field), from pixel centres."""
        if self._centres is None:
            self._centres = [pix2ang_nest(1 << p.order, p.ipix) for p in self.pixels]
        total = sum(
            p.density * p.area
            for p, (pra, pdec) in zip(self.pixels, self._centres)
            if angular_distance_deg(ra, dec, pra, pdec) <= radius_deg
        )
        return total / self.total_prob
