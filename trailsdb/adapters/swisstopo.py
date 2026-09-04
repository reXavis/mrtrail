"""swisstopo: the Swiss hiking network, read without GDAL.

The plan's 65,000 km official figure, published as one GeoPackage on
``data.geo.admin.ch``. Two things about it were not what the plan assumed:

* It is not "351k segments" -- it is **409,276**, and every row is a trail. The
  layer's three ``wanderwege`` values (Wanderweg, Bergwanderweg, Alpinwanderweg)
  account for all of them, so no filtering is needed; the file *is* the filter.
* It does not need GDAL. A GeoPackage is SQLite with WKB inside, and the
  coordinates are LV95, for which swisstopo publishes closed-form conversion
  formulas accurate to about a metre. Both are stdlib work.

Only 824 of the 409,276 rows carry a name. This is network infrastructure --
the width class, surface and difficulty category are what it knows about
itself -- and it renders like the ways layer.

The three categories are Switzerland's signposting classes: yellow, white-red-
white and white-blue-white. They are kept as ``official_status``; a difficulty
distinction this well established is worth showing.

Terms, verified against swisstopo's own page: free of charge as open government
data since 1 March 2021, source reference mandatory, with "Federal Office of
Topography swisstopo" among the prescribed forms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from ..fetch import FetchError
from ..formats import gpkg
from ..manifest import PullManifest
from ..proj import to_wgs84
from ..schema import Feature
from .base import Adapter

COLLECTION = "ch.swisstopo.swisstlm3d-wanderwege"
STAC_ITEMS = f"https://data.geo.admin.ch/api/stac/v0.9/collections/{COLLECTION}/items"

#: Attribute columns worth carrying: width class, surface, bridges/tunnels,
#: traffic restrictions, owner. Everything else in the layer is lineage.
_TRAIL_FACTS = ("objektart", "belagsart", "kunstbaute", "verkehrsbeschraenkung", "eigentuemer")

#: The three Swiss signposting classes, as swisstopo spells them.
_STATUS = {
    "wanderweg": "swiss_wanderweg",
    "bergwanderweg": "swiss_bergwanderweg",
    "alpinwanderweg": "swiss_alpinwanderweg",
}


class SwisstopoAdapter(Adapter):
    name = "swisstopo"
    phase = "4 - Europe wave"

    # -- fetch ---------------------------------------------------------------

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        asset = self.discover_asset()
        record = self.session.download(
            asset["href"], self.raw_dir / Path(asset["href"]).name, force=force
        )
        manifest.add(record)
        manifest.notes = f"asset={asset['key']} {'cached' if record.cached else 'fetched'}"

    def discover_asset(self) -> dict[str, str]:
        """Resolve the current GeoPackage through the STAC API.

        The file name carries a version, so the URL is not stable; the STAC
        collection is. Reading it every pull is what catches a re-publication.
        """
        payload = self.session.get_json(STAC_ITEMS)
        for item in payload.get("features") or []:
            for key, asset in (item.get("assets") or {}).items():
                if "gpkg" in key.lower() and asset.get("href"):
                    return {"key": key, "href": asset["href"], "item": item.get("id", "")}
        raise FetchError(f"{self.source.id}: no GeoPackage asset in the STAC collection {COLLECTION}")

    # -- normalize -----------------------------------------------------------

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        archives = sorted(self.raw_dir.glob("*.gpkg.zip"))
        if not archives:
            raise FileNotFoundError(f"{self.source.id}: no .gpkg.zip in {self.raw_dir}")
        package = gpkg.extract_from_zip(archives[-1], self.raw_dir / "unpacked")
        connection = gpkg.open_gpkg(package)
        try:
            for table in gpkg.feature_tables(connection):
                transform = to_wgs84(table.srs_id)
                for attrs, blob in gpkg.iter_rows(connection, table):
                    feature = self._normalize_row(attrs, blob, transform, manifest, package)
                    if feature is not None:
                        yield feature
        finally:
            connection.close()

    def _normalize_row(self, attrs: dict[str, Any], blob, transform, manifest, package) -> Feature | None:
        geometry = gpkg.lines_to_geometry(gpkg.decode_lines(blob, transform))
        if geometry is None:
            return None
        uuid = str(attrs.get("uuid") or "").strip("{}")
        if not uuid:
            return None

        category = str(attrs.get("wanderwege") or "").strip().lower()
        name = attrs.get("name") or attrs.get("strassenname")
        # Only what describes the trail. The layer's other 20 columns are
        # lineage -- creation dates, revision quality, provenance year -- and on
        # 409,276 segments averaging 160 m they were a third of the file.
        extras = {
            k: attrs[k]
            for k in _TRAIL_FACTS
            if attrs.get(k) not in (None, "", "k_W", "Keine")
        }

        return self.feature(
            uuid,
            geometry,
            manifest=manifest,
            kind="hiking",
            name=str(name) if name else None,
            official_status=_STATUS.get(category, "swiss_wanderweg"),
            country="CH",
            source_url=self.source.homepage or None,
            extras=extras,
        )
