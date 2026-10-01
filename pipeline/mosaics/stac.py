"""Query quarterly Sentinel-2 mosaic assets from public CDSE STAC metadata."""

import json
import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

STAC_SEARCH_URL = "https://stac.dataspace.copernicus.eu/v1/search"
MOSAICS_COLLECTION = "sentinel-2-global-mosaics"
DEFAULT_BANDS = ("B02", "B03", "B04", "B08")


QUARTER_START = {"Q1": "01-01", "Q2": "04-01", "Q3": "07-01", "Q4": "10-01"}
QUARTER_END = {"Q1": "03-31", "Q2": "06-30", "Q3": "09-30", "Q4": "12-31"}


@dataclass
class BandAsset:
    "One band COG of a mosaic item, with both access hrefs and its size."

    band: str
    s3_bucket: str
    s3_key: str
    https_href: str
    size_bytes: int


@dataclass
class MosaicItem:
    "One mosaic item = (MGRS subtile x quarter) with its per-band COG assets."

    item_id: str
    year: int
    quarter: str
    bbox: tuple[float, float, float, float]
    assets: dict[str, BandAsset]

    @property
    def total_bytes(self) -> int:
        return sum(a.size_bytes for a in self.assets.values())

    @property
    def tile_key(self) -> str:
        "MGRS sub-tile key (year/quarter-independent), e.g. ``31UFS_0_0``."
        parts = self.item_id.split("_")
        return "_".join(parts[4:]) if len(parts) > 4 else self.item_id


def _parse_s3(href: str) -> tuple[str, str]:
    "Split an ``s3://bucket/key`` href into (bucket, key)."
    if not href.startswith("s3://"):
        raise ValueError(f"expected s3:// href, got {href!r}")
    rest = href[len("s3://") :]
    bucket, _, key = rest.partition("/")
    return bucket, key


def _item_datetime_range(year: int, quarter: str) -> str:
    return f"{year}-{QUARTER_START[quarter]}T00:00:00Z/{year}-{QUARTER_END[quarter]}T23:59:59Z"


def search_items(
    bbox: tuple[float, float, float, float],
    year: int,
    quarters: tuple[str, ...],
    *,
    bands: tuple[str, ...] = DEFAULT_BANDS,
    session: requests.Session | None = None,
    page_limit: int = 100,
) -> list[MosaicItem]:
    "Return all mosaic items intersecting ``bbox`` for the given year/quarters."
    sess = session or requests.Session()
    items: list[MosaicItem] = []
    for quarter in quarters:
        items.extend(
            _search_one_quarter(sess, bbox, year, quarter, bands=bands, page_limit=page_limit)
        )
    return items


def _next_request(
    link: dict, url: str, body: dict[str, object] | None
) -> tuple[str, str, dict[str, object] | None]:
    """Resolve a STAC ``next`` link into the next (method, url, body).

    Item Search pagination: ``href`` is the URL to call, ``method`` defaults to
    GET, and a ``body`` replaces the previous one unless ``merge`` is true, in
    which case it is merged into it. A GET carries its cursor in the href.
    """
    href = link.get("href") or url
    method = str(link.get("method") or "GET").upper()
    if method == "GET":
        return method, href, None
    link_body = link.get("body")
    if link_body is None:
        return method, href, body
    if link.get("merge"):
        return method, href, {**(body or {}), **link_body}
    return method, href, dict(link_body)


def _matched(payload: dict) -> int | None:
    "Total hits the server reports, from OGC API Features or the context extension."
    for value in (payload.get("numberMatched"), (payload.get("context") or {}).get("matched")):
        if value is not None:
            return int(value)
    return None


def _search_one_quarter(
    sess: requests.Session,
    bbox: tuple[float, float, float, float],
    year: int,
    quarter: str,
    *,
    bands: tuple[str, ...],
    page_limit: int,
) -> list[MosaicItem]:
    body: dict[str, object] | None = {
        "collections": [MOSAICS_COLLECTION],
        "bbox": list(bbox),
        "datetime": _item_datetime_range(year, quarter),
        "limit": page_limit,
    }
    method, url = "POST", STAC_SEARCH_URL
    out: list[MosaicItem] = []
    matched: int | None = None
    seen: set[tuple[str, str, str]] = set()
    while True:
        if method == "GET":
            resp = sess.get(url, timeout=60)
        else:
            resp = sess.post(url, json=body, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if matched is None:
            matched = _matched(payload)
        out.extend(_to_item(feat, year, quarter, bands) for feat in payload.get("features", []))
        next_link = next(
            (link for link in payload.get("links", []) if link.get("rel") == "next"), None
        )
        if not next_link:
            break
        method, url, body = _next_request(next_link, url, body)
        cursor = (method, url, json.dumps(body, sort_keys=True, default=str))
        if cursor in seen:
            raise RuntimeError(
                f"STAC pagination is not advancing for {year} {quarter}: "
                f"{method} {url} repeats after {len(out)} items"
            )
        seen.add(cursor)
    if matched is not None and len(out) != matched:
        raise RuntimeError(
            f"STAC {year} {quarter}: paged {len(out)} items but the server reports "
            f"{matched} matched — the result set is incomplete"
        )
    logger.info("STAC %s %s: %d items in bbox", year, quarter, len(out))
    return out


def _to_item(feat: dict, year: int, quarter: str, bands: tuple[str, ...]) -> MosaicItem:
    assets: dict[str, BandAsset] = {}
    for band in bands:
        a = feat["assets"][band]
        bucket, key = _parse_s3(a["href"])
        https = a.get("alternate", {}).get("https", {}).get("href", "")
        assets[band] = BandAsset(
            band=band,
            s3_bucket=bucket,
            s3_key=key,
            https_href=https,
            size_bytes=int(a.get("file:size", 0)),
        )
    bb = feat["bbox"]
    return MosaicItem(
        item_id=feat["id"],
        year=year,
        quarter=quarter,
        bbox=(bb[0], bb[1], bb[2], bb[3]),
        assets=assets,
    )
