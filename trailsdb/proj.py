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


# -- Transverse Mercator (UTM, SWEREF99 TM, ...) --------------------------------

_GRS80_A = 6_378_137.0
_GRS80_F = 1 / 298.257222101


def tm_inverse(
    east: float,
    north: float,
    *,
    lon0_deg: float,
    k0: float,
    false_east: float,
    false_north: float = 0.0,
    a: float = _GRS80_A,
    f: float = _GRS80_F,
) -> tuple[float, float]:
    """Transverse Mercator projected metres to (lon, lat) degrees.

    The standard series (Krüger n-series to fourth order), good to well under a
    millimetre inside a zone -- the same maths every UTM library carries.
    GRS80 and WGS84 differ by a fraction of a millimetre in flattening, so one
    ellipsoid serves SWEREF99, ETRS89/UTM and WGS84/UTM alike.
    """
    import math

    n = f / (2 - f)
    n2, n3, n4 = n * n, n**3, n**4
    A = a / (1 + n) * (1 + n2 / 4 + n4 / 64)
    beta = (
        n / 2 - 2 * n2 / 3 + 37 * n3 / 96 - n4 / 360,
        n2 / 48 + n3 / 15 - 437 * n4 / 1440,
        17 * n3 / 480 - 37 * n4 / 840,
        4397 * n4 / 161280,
    )
    delta = (
        2 * n - 2 * n2 / 3 - 2 * n3 + 116 * n4 / 45,
        7 * n2 / 3 - 8 * n3 / 5 - 227 * n4 / 45,
        56 * n3 / 15 - 136 * n4 / 35,
        4279 * n4 / 315,
    )

    xi = (north - false_north) / (k0 * A)
    eta = (east - false_east) / (k0 * A)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(beta, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)

    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi
    for j, d in enumerate(delta, start=1):
        lat += d * math.sin(2 * j * chi)
    lon = math.radians(lon0_deg) + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lon), math.degrees(lat)


def sweref99_tm_to_wgs84(east: float, north: float) -> tuple[float, float]:
    """SWEREF99 TM (EPSG:3006): central meridian 15E, k0 0.9996, false easting 500 km."""
    return tm_inverse(east, north, lon0_deg=15.0, k0=0.9996, false_east=500_000.0)


def utm_to_wgs84(zone: int, northern: bool = True):
    """A (E, N) -> (lon, lat) function for a UTM zone on GRS80/WGS84."""

    def convert(east: float, north: float) -> tuple[float, float]:
        return tm_inverse(
            east,
            north,
            lon0_deg=-183.0 + 6 * zone,
            k0=0.9996,
            false_east=500_000.0,
            false_north=0.0 if northern else 10_000_000.0,
        )

    return convert


TRANSFORMS = {
    2056: lv95_to_wgs84,
    3006: sweref99_tm_to_wgs84,
    25832: utm_to_wgs84(32),
    25833: utm_to_wgs84(33),
    25835: utm_to_wgs84(35),
    32632: utm_to_wgs84(32),
    32633: utm_to_wgs84(33),
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
