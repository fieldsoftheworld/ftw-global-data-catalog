"Coverage simplification with GEOS validity repair."

import coarsen
import numpy as np
import shapely


def simplify_coverage(geoms: np.ndarray, tolerance_m: float, label: str = "") -> np.ndarray:
    """Simplify the coverage so shared edges stay shared.

    The whole valid coverage goes through ONE ``coverage_simplify`` pass. Splitting
    it into a good half simplified by ``coverage_simplify`` and a bad half simplified
    by per-geometry Douglas-Peucker at a different tolerance reintroduced exactly the
    defects coverage simplification exists to avoid: measured 26.06 m2 of overlap and
    15.29 m2 of gap across one shared 20-vertex edge, both under
    ``fiboa_convert.JOIN_MIN_OVERLAP_M2`` = 100 m2, so the seam repair downstream
    would never have noticed them. The same fixture through one whole-coverage pass
    measures 0 m2 of each.

    Invalid-coverage geometries therefore stay in the pass rather than being pulled
    out of it, which would remove their neighbours' shared edges from the pass too.
    They are repaired first, individually, and anything still invalid afterwards is
    cleaned up with ``make_valid``, which only nodes and rebuilds -- it never moves a
    vertex, so a shared edge survives it.
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
        # Better an unsimplified coverage than one with fresh gaps: any per-geometry
        # fallback would simplify the two sides of a shared edge independently.
        print(f"  {label or 'coverage'}: not simplified ({exc})", flush=True)
        return out
    inv = ~shapely.is_valid(out)
    if inv.any():
        out[inv] = _polygonal(shapely.make_valid(out[inv]), label)
    return out


def _reject_missing(geoms: np.ndarray, label: str) -> None:
    """Refuse None up front, naming the tile and the offending indices.

    ``coverage_invalid_edges`` does not keep a None input in place: it drops it and
    pads the result with a trailing None, so ``[a, None, b]`` comes back as
    ``[EMPTY, EMPTY, None]`` wherever the None was (verified shapely 2.1.2). Any mask
    taken from that result is misaligned -- a real polygon gets flagged as invalid
    coverage and the None is handed to ``coverage_simplify``, which dies with
    "cannot reshape array of size 2 into shape (3,)". ``_polygonal`` likewise raised
    AttributeError on None. Aligning masks by construction means never letting a
    None into the array in the first place.
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
            # Nothing polygonal survived repair. The column is declared
            # Polygon/MultiPolygon, so the old else-branch wrote a LineString into
            # it; an empty polygon keeps the declared type and is dropped downstream
            # by the NOT ST_IsEmpty(g) filters.
            out[i] = shapely.Polygon()
            dropped += 1
    if dropped:
        print(f"  {label or 'coverage'}: {dropped} geometry with no polygonal part", flush=True)
    return out
