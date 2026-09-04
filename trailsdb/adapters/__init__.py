"""Adapter registry.

Every source in ``sources.yaml`` names an adapter. Three are implemented; the
rest are declared here with the wave of the execution order they belong to, so
``trailsdb status`` can report honestly on what is and is not built rather than
failing with an opaque KeyError.
"""

from __future__ import annotations

from .arcgis import ArcGisAdapter, ArcGisLayer
from .base import Adapter, AdapterContext, AdapterNotImplemented
from .bc_recreation import BcRecreationAdapter
from .cnig import CnigAdapter
from .eurovelo import EuroVeloAdapter
from .kartverket import KartverketAdapter
from .naturvardsverket import NaturvardsverketAdapter
from .nps import NpsAdapter
from .nz_doc import NzDocAdapter
from .ontario_otn import OntarioOtnAdapter
from .swisstopo import SwisstopoAdapter
from .uk_national_trails import UkNationalTrailsAdapter
from .usfs import UsfsAdapter

IMPLEMENTED: dict[str, type[Adapter]] = {
    CnigAdapter.name: CnigAdapter,
    NzDocAdapter.name: NzDocAdapter,
    EuroVeloAdapter.name: EuroVeloAdapter,
    UsfsAdapter.name: UsfsAdapter,
    NpsAdapter.name: NpsAdapter,
    OntarioOtnAdapter.name: OntarioOtnAdapter,
    UkNationalTrailsAdapter.name: UkNationalTrailsAdapter,
    SwisstopoAdapter.name: SwisstopoAdapter,
    KartverketAdapter.name: KartverketAdapter,
    BcRecreationAdapter.name: BcRecreationAdapter,
    NaturvardsverketAdapter.name: NaturvardsverketAdapter,
}

#: adapter name -> the execution-order phase that builds it.
PLANNED: dict[str, str] = {
    "geotrek": "4 - Europe wave",
    "refuges_info": "4 - Europe wave",
    "spain_regional": "4 - Europe wave",
    "mapa_caminos_naturales": "4 - Europe wave",
    "usgs_ndt": "5 - Americas & Oceania wave",
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
    "ArcGisAdapter",
    "ArcGisLayer",
    "BcRecreationAdapter",
    "KartverketAdapter",
    "NaturvardsverketAdapter",
    "UsfsAdapter",
    "AdapterContext",
    "AdapterNotImplemented",
    "CnigAdapter",
    "EuroVeloAdapter",
    "NpsAdapter",
    "NzDocAdapter",
    "OntarioOtnAdapter",
    "SwisstopoAdapter",
    "UkNationalTrailsAdapter",
    "IMPLEMENTED",
    "PLANNED",
    "build",
    "is_implemented",
    "phase_of",
]
