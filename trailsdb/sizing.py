"""The size model, anchored in measured reality rather than guesses.

Every coefficient here was measured on 25 Aug 2026 from the live Galicia pack
pipeline, not estimated:

* 550 OSM routes / 11,176 km / 51.5 points per km
* -> 17.5 MB of enriched GeoJSONL  = **1.56 KB/km**
* -> 39.1 MB of z8-14 PMTiles      = **3.5 KB/km** (including pyramid and gzip)
* = 2.0 % of the 1.96 GB Galicia pack

Those two KB/km numbers are what turn "~810,000 km of official trails" into a
per-pack growth figure, and they are why the answer is "a few percent" rather
than "it depends". Once a source has actually been normalized, ``measured=True``
uses its real length from the catalog instead of the registry's estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Enriched GeoJSONL in the master database. Geometry-dominated: attributes are
#: a rounding error next to coordinate arrays, which is why the figure holds
#: across sources with very different attribute richness.
KB_PER_KM_MASTER = 1.56

#: z8-14 vector tiles, including the zoom pyramid and gzip.
KB_PER_KM_TILES = 3.5

#: Dropping network segments a zoom level (z8-13 instead of z8-14) roughly halves
#: their tile cost. They are infrastructure, not browsable routes, so nothing is
#: lost above the zoom where a user is actually reading trail names.
SEGMENT_Z13_FACTOR = 0.5

#: Catalog row cost: names, refs, status, bbox, no geometry.
KB_PER_CATALOG_ROUTE = 0.6

#: Measured point density of the Galicia routes layer. A source far above this is
#: paying tile bytes for vertices nobody can see; ``geo.simplify`` exists for it.
TARGET_POINTS_PER_KM = 51.5

GALICIA_PACK_BYTES = 1.96 * 1024**3


@dataclass(frozen=True, slots=True)
class SizeEstimate:
    km: float
    master_mb: float
    tiles_mb: float

    def __add__(self, other: "SizeEstimate") -> "SizeEstimate":
        return SizeEstimate(
            self.km + other.km,
            self.master_mb + other.master_mb,
            self.tiles_mb + other.tiles_mb,
        )


def estimate(km: float, *, feature_class: str = "route", cap_segments_at_z13: bool = False) -> SizeEstimate:
    tile_kb_per_km = KB_PER_KM_TILES
    if feature_class == "segment" and cap_segments_at_z13:
        tile_kb_per_km *= SEGMENT_Z13_FACTOR
    return SizeEstimate(
        km=km,
        master_mb=km * KB_PER_KM_MASTER / 1024,
        tiles_mb=km * tile_kb_per_km / 1024,
    )


def pack_growth_percent(tiles_mb: float, pack_bytes: float = GALICIA_PACK_BYTES) -> float:
    return 100.0 * (tiles_mb * 1024**2) / pack_bytes


def catalog_mb(route_count: int) -> float:
    return route_count * KB_PER_CATALOG_ROUTE / 1024
