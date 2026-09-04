"""Coordinate transforms the pipeline needs, without pyproj.

Only one so far. swisstopo publishes everything in LV95 (EPSG:2056), and
its own "approximate formulas" for the conversion to WGS84 are documented to be
accurate to about a metre across Switzerland -- three orders of magnitude finer
than a z14 tile resolves. Implementing them is thirty lines; taking on pyproj
for them is a build dependency on every machine that bakes a pack.
"""

from __future__ import annotations


def lv95_to_wgs84(east: float, north: float) -> tuple[float, float]:
    """CH1903+/LV95 (E, N in metres) to WGS84 (lon, lat in degrees).

    swisstopo's approximate solution. The auxiliary values are the LV95
    coordinates re-centred on Bern and scaled to units of 1000 km; the result
    comes out in units of 10,000 seconds of arc, hence the final ``100 / 36``.
    """
    y = (east - 2_600_000.0) / 1_000_000.0
    x = (north - 1_200_000.0) / 1_000_000.0
    lon_sec = (
        2.6779094
        + 4.728982 * y
        + 0.791484 * y * x
        + 0.1306 * y * x * x
        - 0.0436 * y * y * y
    )
    lat_sec = (
        16.9023892
        + 3.238272 * x
        - 0.270978 * y * y
        - 0.002528 * x * x
        - 0.0447 * y * y * x
        - 0.0140 * x * x * x
    )
    return lon_sec * 100.0 / 36.0, lat_sec * 100.0 / 36.0


TRANSFORMS = {
    2056: lv95_to_wgs84,
}


def to_wgs84(srs_id: int):
    """The (x, y) -> (lon, lat) function for an EPSG code, or None if it is WGS84 already."""
    if srs_id in (4326, 0, -1):
        return None
    try:
        return TRANSFORMS[srs_id]
    except KeyError:
        raise ValueError(
            f"no built-in transform for EPSG:{srs_id}; install the 'geo' extra "
            f"or add one to trailsdb.proj.TRANSFORMS"
        ) from None
