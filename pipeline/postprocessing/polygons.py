"Coverage simplification with GEOS validity repair."

import coarsen
import numpy as np
import shapely


def simplify_coverage(geoms: np.ndarray, tolerance_m: float) -> np.ndarray:
    "Simplify shared edges, falling back on invalid coverage."
    if tolerance_m <= 0 or len(geoms) == 0:
        return geoms
    bad = ~shapely.is_empty(shapely.coverage_invalid_edges(geoms))
    out = np.array(geoms, dtype=object, copy=True)
    if (~bad).any():
        out[~bad] = coarsen.coverage_simplify(geoms[~bad], tolerance_m, threads=1)
    if bad.any():
        out[bad] = shapely.simplify(geoms[bad], min(tolerance_m, 1.2), preserve_topology=True)

    inv = ~shapely.is_valid(out)
    if inv.any():
        out[inv] = _polygonal(shapely.make_valid(out[inv]))
    return out


def _polygonal(geoms: np.ndarray) -> np.ndarray:
    "Keep only the polygonal parts of make_valid output (drops zero-area lines/points)."
    out = np.empty(len(geoms), dtype=object)
    for i, g in enumerate(geoms):
        if g.geom_type in ("Polygon", "MultiPolygon"):
            out[i] = g
        else:
            parts = [
                p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")
            ]
            out[i] = shapely.union_all(parts) if parts else g
    return out
