"""EuroVelo: 17 GPX files, and the one source with a dated attribution notice.

Cheap to pull (minutes) and cheap to write, but it is the source that forces the
attribution machinery to be right: the ECF notice must carry the date the data
was retrieved, so the string stamped on every feature comes from the pull
manifest's ``retrieved_on``, not from whenever a pack happens to be baked.

The route numbering is not contiguous -- there is no EV16 or EV18, and there is
an EV19 -- which is exactly why the table is written out rather than generated
from ``range(1, 18)``.
"""

from __future__ import annotations

from typing import Iterator

from ..fetch import FetchError
from ..formats import archive, gpx
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter

# --- UNVERIFIED: confirm the current file URLs with the ECF before a real pull --
DOWNLOAD_TEMPLATE = "https://eurovelo.com/media/gpx/ev{number}.gpx"
# -------------------------------------------------------------------------------

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


class EuroVeloAdapter(Adapter):
    name = "eurovelo"
    phase = "4 - Europe wave"

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        numbers = sorted(ROUTES)[: limit or len(ROUTES)]
        target = self.raw_dir / "files"
        failures: list[str] = []
        for number in numbers:
            url = DOWNLOAD_TEMPLATE.format(number=number)
            try:
                manifest.add(self.session.download(url, target / f"ev{number}.gpx", force=force))
            except FetchError as exc:
                # One corridor missing is worth recording without losing the
                # other sixteen; an empty pull still fails below.
                failures.append(f"EV{number}: {exc}")

        manifest.errors.extend(failures)
        if not manifest.files:
            raise FetchError(f"{self.source.id}: no EuroVelo files downloaded ({len(failures)} failed)")
        manifest.notes = f"routes={len(manifest.files)}/{len(numbers)}"

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        for path in sorted((self.raw_dir / "files").glob("*.gpx")):
            number = _route_number(path.stem)
            if number is None:
                continue
            official_name = ROUTES.get(number)
            for member, data in archive.iter_gpx(path):
                tracks = gpx.parse(data)
                for index, track in enumerate(tracks):
                    local_id = f"EV{number}" if len(tracks) == 1 else f"EV{number}-{index + 1}"
                    yield self.feature(
                        local_id,
                        track.geometry,
                        manifest=manifest,
                        ref=f"EV{number}",
                        name=track.name or official_name,
                        official_status="eurovelo_developed",
                        source_url=self.source.homepage or None,
                        extras={
                            "source_file": member,
                            "eurovelo_number": number,
                            "official_name": official_name,
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
