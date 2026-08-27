"""EuroVelo: 17 corridors, and the source that settled the licensing model.

Cheap to pull -- 17 files, minutes -- but it is the one that had to be built
early, because its terms are stricter than the plan assumed and they change how
everything else is laid out.

The ECF's own licence document (linked from every route page) says, verbatim:

    These EuroVelo GPX tracks are made available under the Open Database
    License. [...] Attribute: you must attribute any public use of EuroVelo GPX
    tracks, or works produced from EuroVelo GPX tracks, with the following
    notice: "Contains information from EuroVelo GPX tracks downloaded from
    www.EuroVelo.com on [DATE], which is made available here under the Open
    Database License (ODbL)."

Three consequences, all of them load-bearing:

* **ODbL, not a bespoke licence.** It is share-alike, so EuroVelo gets its own
  tile layer and must never be merged with the CC BY sources.
* **The notice is dated**, so the retrieval date comes from the pull manifest
  rather than from whenever a pack happens to be baked.
* **Keep-open**: the ODbL's anti-DRM clause applies to the packs that carry it.
  They are already DRM-free, which is what makes this shippable at all.

Route ids are sparse internal database ids -- EV1 is 2, EV13 is 1, EV14 is 512 --
so they cannot be derived and are discovered from each route's own page.
"""

from __future__ import annotations

import json
import re
from typing import Iterator

from ..fetch import FetchError
from ..formats import archive, gpx
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter

ROUTE_PAGE = "https://en.eurovelo.com/ev{number}"
GPX_URL = "https://en.eurovelo.com/route/get-gpx/{route_id}"
LICENCE_DOCUMENT = (
    "https://pro.eurovelo.com/download/document/"
    "EuroVelo%20GPX%20tracks_License%20and%20disclaimer_20251211.pdf"
)

INDEX_NAME = "routes.json"

#: EuroVelo numbering is not contiguous: there is no EV16 or EV18, and there is
#: an EV19. Seventeen corridors, written out rather than generated.
ROUTES: dict[int, str] = {
    1: "Atlantic Coast Route",
    2: "Capitals Route",
    3: "Pilgrims Route",
    4: "Central Europe Route",
    5: "Via Romea Francigena",
    6: "Atlantic - Black Sea",
    7: "Sun Route",
    8: "Mediterranean Route",
    9: "Baltic - Adriatic",
    10: "Baltic Sea Cycle Route",
    11: "East Europe Route",
    12: "North Sea Cycle Route",
    13: "Iron Curtain Trail",
    14: "Waters of Central Europe",
    15: "Rhine Route",
    17: "Rhone Cycle Route",
    19: "Meuse Cycle Route",
}

_GPX_LINK_RE = re.compile(r"/route/get-gpx/(\d+)")


class EuroVeloAdapter(Adapter):
    name = "eurovelo"
    phase = "4 - Europe wave"

    #: Only the developed sections. The full network includes corridors that are
    #: planned rather than rideable, which is not something to draw on a map a
    #: user navigates by.
    developed_only = True

    # -- fetch ---------------------------------------------------------------

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        route_ids = self.discover()
        if not route_ids:
            raise FetchError(
                f"{self.source.id}: discovered no route ids. Either {INDEX_NAME} is "
                f"missing from {self.raw_dir} or the route pages no longer link "
                f"their GPX downloads."
            )

        numbers = sorted(route_ids)[: limit or len(route_ids)]
        target = self.raw_dir / "files"
        failures: list[str] = []
        for number in numbers:
            url = GPX_URL.format(route_id=route_ids[number])
            if self.developed_only:
                url += "?developed=1"
            try:
                manifest.add(self.session.download(url, target / f"ev{number}.gpx", force=force))
            except FetchError as exc:
                # One corridor missing is worth recording without losing the
                # other sixteen; an empty pull still fails below.
                failures.append(f"EV{number}: {exc}")

        manifest.errors.extend(failures)
        if not manifest.files:
            raise FetchError(
                f"{self.source.id}: no EuroVelo files downloaded ({len(failures)} failed)"
            )
        manifest.notes = (
            f"routes={len(manifest.files)}/{len(numbers)} "
            f"{'developed sections only' if self.developed_only else 'full network'}"
        )

    def discover(self) -> dict[int, str]:
        """Map EuroVelo numbers to the site's internal route ids.

        Cached to ``routes.json`` after the first run: the ids are stable, and a
        resumed pull should not re-scrape 17 pages to learn what it already knows.
        """
        index_path = self.raw_dir / INDEX_NAME
        if index_path.exists():
            raw = json.loads(index_path.read_text(encoding="utf-8"))
            return {int(k): str(v) for k, v in raw.get("routes", raw).items()}

        found: dict[int, str] = {}
        for number in sorted(ROUTES):
            response = self.session.get(ROUTE_PAGE.format(number=number))
            if response.status_code != 200:
                continue
            match = _GPX_LINK_RE.search(getattr(response, "text", "") or "")
            if match:
                found[number] = match.group(1)

        if found:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            index_path.write_text(
                json.dumps({"routes": {str(k): v for k, v in found.items()}}, indent=2),
                encoding="utf-8",
            )
        return found

    # -- normalize -----------------------------------------------------------

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        for path in sorted((self.raw_dir / "files").glob("*.gpx")):
            number = _route_number(path.stem)
            if number is None:
                continue
            official_name = ROUTES.get(number)
            for member, data in archive.iter_gpx(path):
                tracks = gpx.parse(data)
                for index, track in enumerate(tracks):
                    # The ECF ships one corridor as many national sections; each
                    # keeps its own identity under a shared parent route.
                    local_id = f"EV{number}" if len(tracks) == 1 else f"EV{number}-{index + 1}"
                    parent = self.make_id(f"EV{number}") if len(tracks) > 1 else None
                    yield self.feature(
                        local_id,
                        track.geometry,
                        manifest=manifest,
                        ref=f"EV{number}",
                        name=track.name or official_name,
                        parent_id=parent,
                        parent_name=official_name if parent else None,
                        official_status="eurovelo_developed"
                        if self.developed_only
                        else "eurovelo_network",
                        source_url=ROUTE_PAGE.format(number=number),
                        extras={
                            "source_file": member,
                            "eurovelo_number": number,
                            "official_name": official_name,
                            "licence_document": LICENCE_DOCUMENT,
                        },
                    )


def _route_number(stem: str) -> int | None:
    digits = "".join(ch for ch in stem if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:  # pragma: no cover - unreachable given isdigit filter
        return None
