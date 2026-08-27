"""Adapter registry.

Every source in ``sources.yaml`` names an adapter. Three are implemented; the
rest are declared here with the wave of the execution order they belong to, so
``trailsdb status`` can report honestly on what is and is not built rather than
failing with an opaque KeyError.
"""

from __future__ import annotations

from .base import Adapter, AdapterContext, AdapterNotImplemented
from .cnig import CnigAdapter
from .eurovelo import EuroVeloAdapter
from .nz_doc import NzDocAdapter

IMPLEMENTED: dict[str, type[Adapter]] = {
    CnigAdapter.name: CnigAdapter,
    NzDocAdapter.name: NzDocAdapter,
    EuroVeloAdapter.name: EuroVeloAdapter,
}

#: adapter name -> the execution-order phase that builds it.
PLANNED: dict[str, str] = {
    "swisstopo": "4 - Europe wave",
    "geotrek": "4 - Europe wave",
    "kartverket": "4 - Europe wave",
    "naturvardsverket": "4 - Europe wave",
    "uk_national_trails": "4 - Europe wave",
    "refuges_info": "4 - Europe wave",
    "spain_regional": "4 - Europe wave",
    "mapa_caminos_naturales": "4 - Europe wave",
    "usfs": "2 - prove it on three continents",
    "nps": "5 - Americas & Oceania wave",
    "usgs_ndt": "5 - Americas & Oceania wave",
    "ontario_otn": "5 - Americas & Oceania wave",
    "bc_recreation": "5 - Americas & Oceania wave",
    "australia_states": "5 - Americas & Oceania wave",
}


def is_implemented(adapter_name: str) -> bool:
    return adapter_name in IMPLEMENTED


def phase_of(adapter_name: str) -> str:
    cls = IMPLEMENTED.get(adapter_name)
    return cls.phase if cls else PLANNED.get(adapter_name, "unscheduled")


def build(ctx: AdapterContext) -> Adapter:
    """Instantiate the adapter a source names, or explain when it is due."""
    adapter_name = ctx.source.adapter
    cls = IMPLEMENTED.get(adapter_name)
    if cls is None:
        phase = PLANNED.get(adapter_name)
        detail = f"scheduled for phase {phase}" if phase else "not in the execution order"
        raise AdapterNotImplemented(
            f"{ctx.source.id}: adapter {adapter_name!r} is not implemented yet ({detail})"
        )
    return cls(ctx)


__all__ = [
    "Adapter",
    "AdapterContext",
    "AdapterNotImplemented",
    "CnigAdapter",
    "EuroVeloAdapter",
    "NzDocAdapter",
    "IMPLEMENTED",
    "PLANNED",
    "build",
    "is_implemented",
    "phase_of",
]
