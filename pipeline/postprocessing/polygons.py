"Coverage simplification with GEOS validity repair."

import coarsen
import numpy as np
import shapely


def simplify_coverage(geoms: np.ndarray, tolerance_m: float, label: str = "") -> np.ndarray:
    """Simplify the coverage so shared edges stay shared.

    The whole coverage goes through one ``coverage_simplify`` pass. Simplifying the
    coverage-invalid geometries separately with per-geometry Douglas-Peucker at another
    tolerance creates the gaps and overlaps that coverage simplification avoids. On the
    test fixture that gives 26.06 m2 of overlap and 15.29 m2 of gap across one shared
    20-vertex edge, both under ``fiboa_convert.JOIN_MIN_OVERLAP_M2`` = 100 m2, so the
    seam repair downstream would not catch them. One whole-coverage pass gives 0 m2 of
    each.

    Invalid-coverage geometries therefore stay in the pass, since pulling them out would
    also remove their neighbours' shared edges from it. They are repaired first,
    individually, and anything still invalid afterwards is cleaned up with
    ``make_valid``, which only nodes and rebuilds and never moves a vertex, so a shared
    edge survives it.
    """
    if len(geoms) == 0:
        return geoms
    _reject_missing(geoms, label)
    if tolerance_m <= 0:
        return geoms
    out = np.array(geoms, dtype=object, copy=True)
    inv = ~shapely.is_valid(out)
    if inv.any():
        out[inv] = _polygonal(shapely.make_valid(out[inv]), label)
    try:
        out = np.asarray(coarsen.coverage_simplify(out, tolerance_m, threads=1), dtype=object)
    except (shapely.errors.GEOSException, ValueError) as exc:
        # Return the coverage unsimplified rather than fall back per geometry, which would
        # simplify the two sides of a shared edge independently and open gaps.
        print(f"  {label or 'coverage'}: not simplified ({exc})", flush=True)
        return out
    inv = ~shapely.is_valid(out)
    if inv.any():
        out[inv] = _polygonal(shapely.make_valid(out[inv]), label)
    _report_area_drift(geoms, out, label)
    return out


#: Per-parcel relative area change worth reporting. Coverage simplification moves shared
#: edges, which is zero-sum across the coverage, so a large change on one parcel points
#: to a problem. ``is_valid`` alone would pass a valid but wrong result.
AREA_DRIFT_FRAC = 0.25


def _report_area_drift(before: np.ndarray, after: np.ndarray, label: str) -> None:
    a0, a1 = shapely.area(before), shapely.area(after)
    with np.errstate(invalid="ignore", divide="ignore"):
        drift = np.abs(a1 - a0) / np.where(a0 > 0, a0, np.nan)
    bad = np.nonzero(drift > AREA_DRIFT_FRAC)[0]
    if len(bad):
        worst = bad[np.argmax(drift[bad])]
        print(
            f"  {label or 'coverage'}: {len(bad)} parcel(s) changed area by more than "
            f"{AREA_DRIFT_FRAC:.0%} (worst index {worst}: {a0[worst]:.0f} -> "
            f"{a1[worst]:.0f} m2)",
            flush=True,
        )


def _reject_missing(geoms: np.ndarray, label: str) -> None:
    """Refuse None up front, naming the tile and the offending indices.

    ``coverage_invalid_edges`` drops a None input and pads the result with a trailing
    None, so ``[a, None, b]`` comes back as ``[EMPTY, EMPTY, None]`` (shapely 2.1.2). A
    mask taken from that result is misaligned, and ``coverage_simplify`` then fails with
    "cannot reshape array of size 2 into shape (3,)". ``_polygonal`` cannot take None
    either, so None is rejected before any mask is built.
    """
    missing = np.nonzero(shapely.is_missing(geoms))[0]
    if len(missing):
        where = ", ".join(str(i) for i in missing[:10]) + (" ..." if len(missing) > 10 else "")
        raise ValueError(
            f"{label or 'coverage'}: {len(missing)} missing geometry at index {where}"
        )


def _polygonal(geoms: np.ndarray, label: str = "") -> np.ndarray:
    "Keep only the polygonal parts of make_valid output (drops zero-area lines/points)."
    out = np.empty(len(geoms), dtype=object)
    dropped = 0
    for i, g in enumerate(geoms):
        if g.geom_type in ("Polygon", "MultiPolygon"):
            out[i] = g
            continue
        parts = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
        if parts:
            out[i] = shapely.union_all(parts)
        else:
            # Nothing polygonal survived repair. An empty polygon keeps the declared
            # Polygon/MultiPolygon type, and the NOT ST_IsEmpty(g) filters drop it
            # downstream.
            out[i] = shapely.Polygon()
            dropped += 1
    if dropped:
        print(f"  {label or 'coverage'}: {dropped} geometry with no polygonal part", flush=True)
    return out
