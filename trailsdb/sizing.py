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

#: Galicia baseline: enriched GeoJSONL, measured 25 Aug 2026.
KB_PER_KM_MASTER_GALICIA = 1.56

#: What the pipeline actually produces, measured 27 Aug 2026 across 315,767 km
#: of pulled official data (USFS, NZ DOC x2, EuroVelo) -- 39 % of the plan's
#: whole worldwide inventory, so this is a broad sample rather than one region.
#:
#: The Galicia baseline's reasoning holds up: geometry is 87 % of the bytes, and
#: the spread between sources tracks point density almost exactly (EuroVelo at
#: 10.5 points/km costs 0.24 KB/km; USFS at 87.0 costs 2.50). The blended figure
#: sits above Galicia's because the pulled mix is denser than Galicia's
#: 51.5 points/km, not because the model is wrong.
KB_PER_KM_MASTER = 1.98

#: z8-14 vector tiles, including the zoom pyramid and gzip -- the Galicia OSM
#: routes layer, measured 25 Aug 2026. Kept for reference; it is not what this
#: pipeline's tiles cost.
KB_PER_KM_TILES_GALICIA = 3.5

#: What the pipeline's own tiles cost, measured 4 Sep 2026 by baking four packs
#: with tippecanoe at the settings tippecanoe_args() records (routes z8-14,
#: segments z8-13), on tile-shaped features carrying only TILE_ATTRIBUTES:
#:
#:   New Zealand   routes    13,687 km   5.0 MB   0.37 KB/km
#:   Pyrenees      routes    20,851 km   6.3 MB   0.31 KB/km   (Camino + Geotrek)
#:   EuroVelo      routes     2,943 km   1.2 MB   0.43 KB/km
#:   New Zealand   segments  13,819 km   5.0 MB   0.37 KB/km
#:   Colorado      segments  22,987 km   7.9 MB   0.35 KB/km   (USFS + NPS)
#:   Switzerland   segments  66,926 km 105.5 MB   1.61 KB/km   (409,276 pieces, 160 m each)
#:
#: Routes cluster tightly. Segments do not: cost is driven by feature count
#: rather than length, because each piece carries its attributes into every
#: tile, and swisstopo publishes trails in 160 m pieces. The segment figure is
#: therefore set near that worst case rather than the median -- an estimate
#: for a pack that turns out to be swisstopo-shaped should not be four times
#: too low. Per-source measured figures, where a source has been baked, are in
#: the export's bake.json and beat these defaults.
KB_PER_KM_TILES_ROUTE = 0.4
KB_PER_KM_TILES_SEGMENT = 1.2

#: The figure the plan carried, kept only so old comparisons still read.
KB_PER_KM_TILES = KB_PER_KM_TILES_GALICIA

#: The segment zoom lever is now baked into KB_PER_KM_TILES_SEGMENT (the
#: measurements were taken at z13), so it no longer applies on top.
SEGMENT_Z13_FACTOR = 1.0

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


#: Spots have no length, so they are priced per feature. Provisional until
#: refuges.info has been normalized and baked; both are then re-measured.
KB_PER_SPOT_MASTER = 0.35
KB_PER_SPOT_TILES = 0.15


def estimate(
    km: float,
    *,
    feature_class: str = "route",
    cap_segments_at_z13: bool = False,
    features: int = 0,
) -> SizeEstimate:
    """Master-database and tile bytes for ``km`` of a feature class.

    ``cap_segments_at_z13`` is accepted for compatibility; the measured segment
    coefficient was taken at z13 already, so it changes nothing. Spots carry no
    km; they are priced from ``features``.
    """
    if feature_class == "spot":
        return SizeEstimate(
            km=0.0,
            master_mb=features * KB_PER_SPOT_MASTER / 1024,
            tiles_mb=features * KB_PER_SPOT_TILES / 1024,
        )
    tile_kb_per_km = KB_PER_KM_TILES_SEGMENT if feature_class == "segment" else KB_PER_KM_TILES_ROUTE
    return SizeEstimate(
        km=km,
        master_mb=km * KB_PER_KM_MASTER / 1024,
        tiles_mb=km * tile_kb_per_km / 1024,
    )


def pack_growth_percent(tiles_mb: float, pack_bytes: float = GALICIA_PACK_BYTES) -> float:
    return 100.0 * (tiles_mb * 1024**2) / pack_bytes


def catalog_mb(route_count: int) -> float:
    return route_count * KB_PER_CATALOG_ROUTE / 1024
