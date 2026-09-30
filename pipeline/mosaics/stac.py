"""Query quarterly Sentinel-2 mosaic assets from public CDSE STAC metadata."""

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


def _search_one_quarter(
    sess: requests.Session,
    bbox: tuple[float, float, float, float],
    year: int,
    quarter: str,
    *,
    bands: tuple[str, ...],
    page_limit: int,
) -> list[MosaicItem]:
    body: dict[str, object] = {
        "collections": [MOSAICS_COLLECTION],
        "bbox": list(bbox),
        "datetime": _item_datetime_range(year, quarter),
        "limit": page_limit,
    }
    out: list[MosaicItem] = []
    while True:
        resp = sess.post(STAC_SEARCH_URL, json=body, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        out.extend(_to_item(feat, year, quarter, bands) for feat in payload.get("features", []))
        next_link = next(
            (link for link in payload.get("links", []) if link.get("rel") == "next"), None
        )
        if not next_link:
            break

        body = {**body, **next_link.get("body", {})}
        if "token" not in body and "token" not in next_link.get("body", {}):
            break
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
