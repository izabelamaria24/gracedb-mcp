"""GPS <-> UTC conversion without gwpy/astropy, so it also works in the slim image."""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta, timezone

GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)

# GPS times at which each leap second took effect (GPS - UTC grows by one at each).
# No leap second has been added since 2017-01-01; IERS announces any new one months ahead.
_LEAP_GPS = [
    46828800, 78364801, 109900802, 173059203, 252028804, 315187205, 346723206,
    393984007, 425520008, 457056009, 504489610, 551750411, 599184012, 820108813,
    914803214, 1025136015, 1119744016, 1167264017,
]


def gps_to_utc(gps: float) -> datetime:
    leaps = bisect.bisect_right(_LEAP_GPS, gps)
    return GPS_EPOCH + timedelta(seconds=gps - leaps)


def utc_to_gps(utc: datetime) -> float:
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=timezone.utc)
    naive = (utc - GPS_EPOCH).total_seconds()
    # Leap seconds are counted at their GPS time, so apply them in GPS terms.
    leaps = bisect.bisect_right(_LEAP_GPS, naive + bisect.bisect_right(_LEAP_GPS, naive))
    return naive + leaps


def iso(gps: float) -> str:
    return gps_to_utc(gps).strftime("%Y-%m-%d %H:%M:%S UTC")
