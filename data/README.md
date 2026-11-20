# Records used in the paper

| File | One row per | Fields used in the analysis |
| --- | --- | --- |
| `cities.csv` | Selected site | `sampling_unit` identifies the site. The `regional`, `prior`, and `anchor` indicators can overlap. The probability columns describe the city draw. `human_submitted` counts completed human reviews. |
| `locations.csv.gz` | Sampled road position | Latitude and longitude locate the random point on the mapped route. Rank gives its position in the seeded order within the city. A shared `panorama_key` means two points matched the same panorama. Blank keys denote unsuccessful lookups. `coverage_state` distinguishes a failed request from a search that found no panorama. |
| `images.csv.gz` | Acquired image | `batch` identifies the first or supplemental draw. `capture_year` comes from the provider. `n_person` is the detector count. Five Muse fields count apparent women and men walking or standing, women and men riding two-wheelers, and people whose classification was unclear. |

The city lottery used seed 20261118. The road draw used seed 42 and the November 15, 2026 OpenStreetMap US Layercake highways snapshot. The coordinates in the location file are the sampled map points. The field study contributed five anchor sites. Its participants’ GPS tracks stay with the field study. [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).

The indicator `reference_within_50m` records whether a camera in the Global Streetscapes catalog lay within 50 meters of the sampled point. The [spatial match](../scripts/collection/09_reference_coverage.py) used the catalog’s common-attributes Parquet file at revision `7bd2e7697a3cb5f74ff05bd718babdb927f8b60d`. The public files use anonymous panorama keys so readers can count distinct panoramas. Google imagery and individual human review records are held separately. [Global Streetscapes catalog](https://huggingface.co/datasets/NUS-UAL/global-streetscapes/tree/7bd2e7697a3cb5f74ff05bd718babdb927f8b60d).
