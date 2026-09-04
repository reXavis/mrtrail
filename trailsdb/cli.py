"""trailsdb -- the command line over the pipeline.

    trailsdb status                       what is built, pulled and normalized
    trailsdb health [sources...]          are the endpoints still there
    trailsdb pull cnig --limit 5          download a source (or a whole adapter)
    trailsdb normalize cnig_fedme         raw tier -> master database
    trailsdb export --pack galicia        cut a pack, ready for tippecanoe
    trailsdb licenses -o licenses.json    the app's data-sources screen payload
    trailsdb estimate                     the size model, from the registry
    trailsdb search "Camino Frances"      look through the catalog

Source selection is the same everywhere: give source ids (``cnig_fedme``),
adapter names (``cnig``, which expands to all three series), or nothing at all
for every source.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from . import packs as packs_module
from . import pipeline, registry as registry_module, sizing
from .adapters import AdapterNotImplemented, is_implemented
from .catalog import Catalog
from .config import Paths
from .registry import RegistryError, Source


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2

    registry = registry_module.load(args.registry)
    paths = Paths.resolve(args.data).ensure()

    try:
        return args.handler(args, registry, paths)
    except RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2
    except AdapterNotImplemented as exc:
        print(f"{exc}", file=sys.stderr)
        return 3
    except BrokenPipeError:
        # `trailsdb status | head` is a normal thing to do. Redirect stdout to
        # devnull so the interpreter does not complain again while flushing.
        _silence_broken_pipe()
        return 0


def _silence_broken_pipe() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):  # pragma: no cover - stdout already gone
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trailsdb", description=__doc__.split("\n")[0])
    parser.add_argument("--data", metavar="DIR", help="data root (default: $TRAILSDB_DATA or ./data)")
    parser.add_argument("--registry", metavar="FILE", help="alternative sources.yaml")
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="what is built, pulled and normalized")
    p_status.set_defaults(handler=cmd_status)

    p_health = sub.add_parser("health", help="check every source endpoint is still there")
    p_health.add_argument("sources", nargs="*")
    p_health.set_defaults(handler=cmd_health)

    p_pull = sub.add_parser("pull", help="download sources into the raw tier")
    p_pull.add_argument("sources", nargs="*")
    p_pull.add_argument("--force", action="store_true", help="re-download even if unchanged")
    p_pull.add_argument("--limit", type=int, help="stop after N files (for smoke tests)")
    p_pull.add_argument(
        "--resume",
        action="store_true",
        help="trust files already on disk instead of revalidating them with the server",
    )
    p_pull.set_defaults(handler=cmd_pull)

    p_norm = sub.add_parser("normalize", help="raw tier -> master database + catalog")
    p_norm.add_argument("sources", nargs="*")
    p_norm.set_defaults(handler=cmd_normalize)

    p_export = sub.add_parser("export", help="cut a region pack out of the master database")
    p_export.add_argument("--pack", required=True, help="pack id from packs.yaml, or any name with --bbox")
    p_export.add_argument(
        "--bbox",
        metavar="W,S,E,N",
        help="west,south,east,north, overriding packs.yaml. Write it as --bbox=-9,41,-6,44: "
        "with a space, a negative west longitude is read as another option.",
    )
    p_export.add_argument("sources", nargs="*")
    p_export.add_argument(
        "--allow-unverified",
        action="store_true",
        help="include sources whose license and attribution are not confirmed yet",
    )
    p_export.set_defaults(handler=cmd_export)

    p_bake = sub.add_parser("bake", help="run tippecanoe over a pack's exported layers")
    p_bake.add_argument("--pack", required=True)
    p_bake.add_argument("--layer", action="append", help="bake only this layer (repeatable)")
    p_bake.set_defaults(handler=cmd_bake)

    p_lic = sub.add_parser("licenses", help="the app's data-sources screen payload")
    p_lic.add_argument("-o", "--output", help="write JSON here instead of stdout")
    p_lic.set_defaults(handler=cmd_licenses)

    p_est = sub.add_parser("estimate", help="size model over the registry or a pack")
    p_est.add_argument("--pack", help="report growth against this pack's size")
    p_est.add_argument(
        "--cap-segments-at-z13",
        action="store_true",
        help="apply the segment zoom lever (roughly halves segment tile cost)",
    )
    p_est.set_defaults(handler=cmd_estimate)

    p_search = sub.add_parser("search", help="search the catalog by name or ref")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.set_defaults(handler=cmd_search)

    return parser


def _select(registry, args) -> list[Source]:
    return registry.select(getattr(args, "sources", None) or None)


# ------------------------------------------------------------------ commands --


def cmd_status(args, registry, paths: Paths) -> int:
    catalog = Catalog(paths.catalog) if paths.catalog.exists() else None
    try:
        rows = pipeline.status(registry, paths, catalog=catalog)
    finally:
        if catalog:
            catalog.close()

    header = f"{'source':<24} {'adapter':<10} {'pulled':>7} {'features':>9} {'km':>10}  legal   phase"
    print(header)
    print("-" * len(header))
    for row in rows:
        adapter_mark = "ready" if row.adapter_ready else "planned"
        legal_mark = "ok" if row.verified else "UNVERIF"
        print(
            f"{row.source.id:<24} {adapter_mark:<10} {row.pulled_files:>7} "
            f"{row.normalized_features:>9} {row.normalized_km:>10.0f}  {legal_mark:<7} {row.phase}"
        )

    ready = sum(1 for r in rows if r.adapter_ready)
    verified = sum(1 for r in rows if r.verified)
    total_km = sum(r.normalized_km for r in rows)
    print()
    print(
        f"{ready}/{len(rows)} adapters implemented, {verified}/{len(rows)} sources legally verified, "
        f"{total_km:,.0f} km normalized of {registry.total_estimated_km:,} km estimated"
    )
    return 0


def cmd_health(args, registry, paths: Paths) -> int:
    results = pipeline.health_check(_select(registry, args))
    failures = 0
    for result in results:
        if result.ok:
            print(f"ok      {result.source_id:<24} {result.status} {result.url}")
        else:
            failures += 1
            detail = result.error or f"HTTP {result.status}"
            print(f"FAIL    {result.source_id:<24} {detail} {result.url}")
    print(f"\n{len(results) - failures}/{len(results)} sources reachable")
    return 1 if failures else 0


def cmd_pull(args, registry, paths: Paths) -> int:
    selected = _select(registry, args)
    failures = 0
    with Catalog(paths.catalog) as catalog:
        for source in selected:
            if not is_implemented(source.adapter):
                print(f"skip    {source.id:<24} adapter not implemented yet")
                continue
            print(f"pull    {source.id:<24} (pacing {source.rate_limit_s}s/request)", flush=True)
            manifest = pipeline.pull_source(
                source,
                paths,
                force=args.force,
                limit=args.limit,
                resume=args.resume,
                catalog=catalog,
            )
            status = "ok" if manifest.ok else "ERRORS"
            print(
                f"  {status:<6} {len(manifest.files)} files, {manifest.changed_files} changed, "
                f"{manifest.total_bytes / 1024 / 1024:.1f} MB  {manifest.notes}"
            )
            for warning in manifest.warnings:
                print(f"  warn   {warning}")
            for error in manifest.errors:
                failures += 1
                print(f"  error  {error}")
    return 1 if failures else 0


def cmd_normalize(args, registry, paths: Paths) -> int:
    selected = _select(registry, args)
    failures = 0
    with Catalog(paths.catalog) as catalog:
        for source in selected:
            if not is_implemented(source.adapter):
                continue
            if not (paths.raw_dir(source.id)).exists():
                print(f"skip    {source.id:<24} nothing pulled yet")
                continue
            try:
                result = pipeline.normalize_source(source, paths, catalog=catalog)
            except Exception as exc:
                failures += 1
                print(f"FAIL    {source.id:<24} {type(exc).__name__}: {exc}")
                continue
            density = (
                f"{result.kb_per_feature:5.2f} KB/spot"
                if result.features and not result.length_km
                else f"{result.points_per_km:5.1f} pts/km  {result.kb_per_km:5.2f} KB/km"
            )
            print(
                f"ok      {result.source_id:<24} {result.features:>7,} features "
                f"{result.length_km:>9,.0f} km  {density}"
            )
    if failures:
        print(f"\n{failures} source(s) failed to normalize")
    return 1 if failures else 0


def cmd_export(args, registry, paths: Paths) -> int:
    if args.bbox:
        try:
            bbox = packs_module.parse_bbox(args.bbox)
        except ValueError as exc:
            print(f"bad --bbox: {exc}", file=sys.stderr)
            return 2
        pack_gb = 0.0
    else:
        known = packs_module.load()
        if args.pack not in known:
            print(
                f"unknown pack {args.pack!r}; known: {', '.join(sorted(known))}. "
                f"Pass --bbox W,S,E,N for anything else.",
                file=sys.stderr,
            )
            return 2
        bbox = known[args.pack].bbox
        pack_gb = known[args.pack].pack_gb

    result = pipeline.export_pack(
        registry,
        paths,
        pack=args.pack,
        bbox=bbox,
        sources=_select(registry, args),
        allow_unverified=args.allow_unverified,
    )

    print(f"pack {result.pack}  bbox {', '.join(f'{v:g}' for v in result.bbox)}")
    for layer in result.layers:
        print(
            f"  {layer.layer:<18} {layer.features:>7,} features {layer.length_km:>9,.0f} km  "
            f"~{layer.estimated_tiles_mb:6.1f} MB tiles   sources: {', '.join(layer.sources)}"
        )
        print(f"    tippecanoe {' '.join(pipeline.tippecanoe_args(layer))} {layer.path.name}")
    for source_id, reason in result.skipped:
        print(f"  skipped {source_id:<18} {reason}")

    if result.layers:
        total = result.estimated_tiles_mb
        growth = f", +{sizing.pack_growth_percent(total, pack_gb * 1024 ** 3):.1f}% of pack" if pack_gb else ""
        print(f"\ntotal ~{total:.1f} MB of tiles{growth}")
        print(f"written to {paths.export_dir(result.pack)}")
    else:
        print("\nnothing exported")
    return 0


def cmd_bake(args, registry, paths: Paths) -> int:
    if not pipeline.tippecanoe_available():
        print("tippecanoe is not on PATH; install it to bake", file=sys.stderr)
        return 2
    export = paths.export_dir(args.pack) / "export.json"
    if not export.exists():
        print(f"no export for pack {args.pack!r}; run `trailsdb export --pack {args.pack}` first", file=sys.stderr)
        return 2
    result = pipeline.bake_pack(paths, pack=args.pack, layers=args.layer)
    print(f"pack {result.pack}")
    print(f"  {'layer':<18}{'class':<9}{'features':>9}{'km':>10}{'MB':>8}{'KB/km':>8}{'s':>6}")
    for b in result.layers:
        print(
            f"  {b.layer:<18}{b.feature_class:<9}{b.features:>9,}{b.length_km:>10,.0f}"
            f"{b.megabytes:>8.1f}{(b.kb_per_km if b.length_km else b.kb_per_feature):>8.2f}"
            f"{b.seconds:>6.0f}{'  KB/spot' if not b.length_km else ''}"
        )
    print(
        f"\ntotal {result.total_bytes / 1024**2:.1f} MB of tiles  "
        f"(model: {sizing.KB_PER_KM_TILES_ROUTE_FIT} KB/km + {sizing.KB_PER_FEATURE_TILES_ROUTE} KB/feature "
        f"for routes, {sizing.KB_PER_KM_TILES_SEGMENT_FIT} + {sizing.KB_PER_FEATURE_TILES_SEGMENT} for segments, "
        f"{sizing.KB_PER_SPOT_TILES} KB/spot; plan carried {sizing.KB_PER_KM_TILES_GALICIA} KB/km)"
    )
    return 0


def cmd_licenses(args, registry, paths: Paths) -> int:
    document = pipeline.licenses_document(registry)
    text = json.dumps(document, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        unverified = [s["id"] for s in document["sources"] if not s["verified_on"]]
        print(f"wrote {args.output} ({len(document['sources'])} sources)")
        if unverified:
            print(f"note: {len(unverified)} not legally verified yet: {', '.join(unverified)}")
    else:
        print(text)
    return 0


def cmd_estimate(args, registry, paths: Paths) -> int:
    catalog = Catalog(paths.catalog) if paths.catalog.exists() else None
    stats = list(catalog.stats()) if catalog else []
    measured = {s.source_id: s.length_km for s in stats}
    counted = {s.source_id: s.features for s in stats}
    if catalog:
        catalog.close()

    header = f"{'source':<24} {'km':>9} {'src':>9} {'master MB':>10} {'tiles MB':>9}"
    print(header)
    print("-" * len(header))
    total = sizing.SizeEstimate(0.0, 0.0, 0.0)
    for source in sorted(registry, key=lambda s: -s.estimated_km):
        km = measured.get(source.id) or float(source.estimated_km)
        features = counted.get(source.id, 0)
        origin = (
            "measured"
            if (measured.get(source.id) or (source.feature_class == "spot" and features))
            else source.km_confidence
        )
        est = sizing.estimate(
            km,
            feature_class=source.feature_class,
            cap_segments_at_z13=args.cap_segments_at_z13,
            features=features,
        )
        total = total + est
        print(f"{source.id:<24} {km:>9,.0f} {origin:>9} {est.master_mb:>10.1f} {est.tiles_mb:>9.1f}")

    print("-" * len(header))
    print(f"{'total':<24} {total.km:>9,.0f} {'':>9} {total.master_mb:>10.1f} {total.tiles_mb:>9.1f}")
    print(
        f"\nmaster database ~{total.master_mb / 1024:.2f} GB, "
        f"all tiles worldwide ~{total.tiles_mb / 1024:.2f} GB"
    )
    if args.pack:
        known = packs_module.load()
        if args.pack in known:
            pack = known[args.pack]
            print(
                f"for reference, {pack.name} ships at {pack.pack_gb:.2f} GB "
                f"(coefficients: {sizing.KB_PER_KM_MASTER} KB/km master; "
                f"{sizing.KB_PER_KM_TILES_ROUTE} KB/km route tiles, "
                f"{sizing.KB_PER_KM_TILES_SEGMENT} KB/km segment tiles -- measured on this pipeline)"
            )
    return 0


def cmd_search(args, registry, paths: Paths) -> int:
    if not paths.catalog.exists():
        print("no catalog yet -- run `trailsdb normalize` first", file=sys.stderr)
        return 2
    with Catalog(paths.catalog) as catalog:
        rows = catalog.search(args.query, limit=args.limit)
        for row in rows:
            ref = f"[{row['ref']}] " if row["ref"] else ""
            print(f"{row['id']:<36} {ref}{row['name'] or '(unnamed)'}  {row['length_km']:.1f} km")
        print(f"\n{len(rows)} result(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
