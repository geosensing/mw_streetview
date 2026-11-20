# How the records were collected

| Stage | Source | Inputs |
| --- | --- | --- |
| City draw | [cities.py](cities.py), [sampling.py](sampling.py) | Urban center polygons, prior-study site list, bike-study anchor crosswalk, and regional strata. The original frame preparation reads field records. |
| Road draw | [05_sample_city_roads.py](05_sample_city_roads.py), [route access rules](gsview/route_access.py) | Selected city polygons and the Layercake OpenStreetMap highways snapshot. The expanded draw used the pedestrian access rules and 1,000 points per site. |
| Panorama lookup and acquisition | [07_review_candidates.py](07_review_candidates.py), [12_google_review_images.py](12_google_review_images.py) | Ranked road positions, Google Street View API credentials, and a request budget. Searches measured availability for every sampled point. |
| Detection | [16_detect_people.py](16_detect_people.py) | Acquired image files, acquisition ledger, and YOLO weights. |
| Counts and reviews | [14_google_muse.py](14_google_muse.py), [13_queue_google_reviews.py](13_queue_google_reviews.py) | The Muse script reads detector-positive records and calls the companion project's counting script. The human review queue uses Label Studio. |
| Independent camera coverage | [09_reference_coverage.py](09_reference_coverage.py) | The sampled coordinates and the pinned Global Streetscapes camera catalog. |

These are the source scripts from the collection environment. Running collection requires the original study directory layout and stage-specific inputs. For the published tables and figure, use the root Makefile. The [export script](../export_records.py) shows how the three public data files were made from the collection ledgers.
