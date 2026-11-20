# FTW field boundaries 2018

207,793,034 parcels in 60 UTM-zone files.

The extraction recipe uses mean field probability 0.10, fills enclosed holes strictly smaller than 100 m² and requires at least 900 m². Independent crop filtering evaluates parcels from 900 to 5,000,000 m² and removes those with less than 5% overlap with the native 2017/2020/2024 Impact Observatory crop union; unknown or incomplete observations and parcels outside those size bounds are retained by this filter. The embedded processing description below also records downstream vector cleanup, including its separate 20 m² hole rule. These stages have different purposes and thresholds.

Embedded vector provenance, unchanged:

Fields of The World (FTW) model on Sentinel-2 quarterly cloudless mosaics (CDSE sentinel-2-global-mosaics, 2018 Q1-Q4, 4 quarters x B04/B03/B02/B08), 2.5 m field/boundary probabilities, BoundaryVote instance post-processing (nbg-pb-h0.01-t0.5+R10+F10+G2+H100lt+A900+q1), 5 m coverage simplification, parcels > 5 km2 removed, parcels at least 70% Impact Observatory io-lulc 2024 water (inland water, aquaculture ponds, salt pans) removed, parcels with less than 50% of their area on OpenStreetMap land polygons (sea) removed, parcels and parts under 900 m2 removed, interior holes under 20 m2 filled. Attributes are for filtering.

[Collection](https://data.source.coop/ftw/global-data-2e/vector/2018/collection.json) lists the individual files, checksums, PMTiles, styles and corrected STAC GeoParquet mirror.

The nine A5 assets include the established resolution-7 aggregate and separate web aggregates at resolutions 4-11; they retain their original metrics.

Data licence: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
