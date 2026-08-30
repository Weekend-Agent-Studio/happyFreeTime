# HappyFreeTime Demo World V1

`enrichment.json` is a versioned, deterministic business-world overlay for the
200 records in `data/catalog/pois.json`.  It deliberately keeps each OSM
`resource_id`, name and coordinate anchor unchanged so route geometry and map
placement remain meaningful.

| Layer | Meaning |
| --- | --- |
| SourceFact | OSM snapshot: name, category, address when supplied, coordinate and image attribution |
| DerivedFeature | GCJ-02 conversion, distance, route legs, time-line and planning score |
| SimulatedState | Reference price/rating/reviews, hours, facilities, child/weather suitability, queue and reservation copy |

The simulated fields are stable from `resource_id + v1 seed`; this is a
portfolio/demo data model, not merchant information and not a live inventory
feed.  It includes no phone numbers, merchant websites or booking URLs.

Build or validate it with:

```powershell
python scripts/build_demo_world.py
python scripts/validate_demo_world.py
```

Existing Wikimedia images remain source images with their original attribution.
Missing images use a local category illustration marked `illustrative`, never a
claim of a real storefront photo.  The UI gives one restrained notice: “演示环境：
POI 商业信息为模拟数据，地图与路线来自高德。”
