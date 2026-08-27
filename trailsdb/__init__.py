"""trailsdb -- the world trail database pipeline.

Joins every legally usable official trail source into one normalized dataset,
cut per region pack and baked into the packs' vector tiles alongside the existing
OpenStreetMap routes layer.

The layers stay separate by design: official and community data are never merged
geometrically, because that would create a derivative database across
incompatible licenses. They are cross-linked instead.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import Paths  # noqa: E402
from .registry import Registry, Source, load as load_registry  # noqa: E402
from .schema import Feature  # noqa: E402

__all__ = ["Paths", "Registry", "Source", "Feature", "load_registry", "__version__"]
