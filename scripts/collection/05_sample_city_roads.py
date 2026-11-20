"""Sample regional road locations from the spatially indexed OSM US highways snapshot."""

import csv
import hashlib
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryFile

import click
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
import shapely
from gsview.cities import GEOD, atomic_path, read_centers
from gsview.route_access import prepare_routes

URL = "https://data.openstreetmap.us/layercake/highways.parquet"
CLASSES = ["primary", "secondary", "tertiary", "residential", "unclassified"]
EXPANDED = CLASSES + [
    "trunk",
    "pedestrian",
    "living_street",
    "service",
    "footway",
    "path",
    "steps",
    "primary_link",
    "secondary_link",
    "tertiary_link",
    "trunk_link",
    "motorway",
    "motorway_link",
]


def byte_range(start, end, etag):
    for attempt in range(5):
        try:
            response = requests.get(
                URL,
                headers={"Range": f"bytes={start}-{end}", "If-Match": etag},
                timeout=(15, 120),
            )
            response.raise_for_status()
            if (
                response.status_code != 206
                or len(response.content) != end - start + 1
                or response.headers["ETag"] != etag
            ):
                raise ValueError("Unexpected snapshot or incomplete byte range")
            return response.content
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(2**attempt)


def load_index(folder):
    index = folder / "index.parquet"
    info = folder / "source.json"
    if index.exists() and info.exists():
        return pq.read_metadata(index), json.loads(info.read_text())
    header = requests.head(URL, timeout=30)
    header.raise_for_status()
    snapshot = {
        "url": URL,
        "etag": header.headers["ETag"],
        "modified": header.headers["Last-Modified"],
        "bytes": int(header.headers["Content-Length"]),
    }
    if info.exists() and json.loads(info.read_text()) != snapshot:
        raise ValueError("Remote snapshot changed. Use a new run directory.")
    with atomic_path(info) as temp:
        temp.write_text(json.dumps(snapshot, indent=2) + "\n")
    if not index.exists():
        size = snapshot["bytes"]
        tail = byte_range(size - 8, size - 1, snapshot["etag"])
        if tail[4:] != b"PAR1":
            raise ValueError("Invalid Parquet footer")
        length = int.from_bytes(tail[:4], "little")
        footer = byte_range(size - 8 - length, size - 9, snapshot["etag"])
        with atomic_path(index) as temp:
            temp.write_bytes(b"PAR1" + footer + tail)
    return pq.read_metadata(index), snapshot


def city_groups(meta, cities):
    groups = {}
    for i in range(meta.num_row_groups):
        rg = meta.row_group(i)
        stats = {
            col.path_in_schema: col.statistics
            for j in range(rg.num_columns)
            if (col := rg.column(j)).path_in_schema.startswith("bbox.")
        }
        bounds = [
            stats["bbox.xmin"].min,
            stats["bbox.ymin"].min,
            stats["bbox.xmax"].max,
            stats["bbox.ymax"].max,
        ]
        hits = [
            key
            for key, (_, area) in cities.items()
            if shapely.intersects(shapely.box(*bounds), shapely.box(*area.bounds))
        ]
        if hits:
            groups[i] = hits
    return groups


def extract_group(i, keys, cities, meta, snapshot, folder, expanded=False):
    marker = folder / f"{i}.done"
    if marker.exists():
        return
    rg = meta.row_group(i)
    first = rg.column(0)
    last = rg.column(rg.num_columns - 1)
    start = first.dictionary_page_offset or first.data_page_offset
    end = (
        last.dictionary_page_offset or last.data_page_offset
    ) + last.total_compressed_size
    # Parquet offsets are absolute. A sparse buffer exposes just this row group to Arrow.
    tags = (
        ["foot", "access", "motor_vehicle", "sidewalk", "service", "footway"]
        if expanded
        else []
    )
    with TemporaryFile() as file:
        file.seek(start)
        file.write(byte_range(start, end - 1, snapshot["etag"]))
        file.flush()
        table = pq.ParquetFile(file, metadata=meta).read_row_group(
            i, columns=["id", "type", "highway", "name", "bbox", "geometry", *tags]
        )
    roads = table.filter(
        pa.compute.and_(
            pa.compute.equal(table["type"], "way"),
            pa.compute.is_in(
                table["highway"], value_set=pa.array(EXPANDED if expanded else CLASSES)
            ),
        )
    )
    bb = roads["bbox"].combine_chunks()
    xmin, ymin, xmax, ymax = (
        bb.field(k).to_numpy() for k in ("xmin", "ymin", "xmax", "ymax")
    )
    for key in keys:
        x, y, z, t = cities[key][1].bounds
        nearby = roads.filter((xmin <= z) & (xmax >= x) & (ymin <= t) & (ymax >= y))
        if len(nearby):
            dest = folder / f"{key}_{i}.parquet"
            with atomic_path(dest) as tmp:
                pq.write_table(
                    nearby.select(["id", "highway", "name", "geometry", *tags]), tmp
                )
    marker.touch()


def line_arrays(geoms, indexes):
    kinds = shapely.get_type_id(geoms)
    lines = kinds == 1
    if lines.any():
        yield geoms[lines], indexes[lines]
    collections = np.isin(kinds, [5, 7])
    if collections.any():
        parts, parents = shapely.get_parts(geoms[collections], return_index=True)
        yield from line_arrays(parts, indexes[collections][parents])


def draw(key, city, area, files, count, seed, expanded=False):
    if not files:
        return None, 0, []
    roads = pa.concat_tables([pq.read_table(p) for p in files]).sort_by(
        [("id", "ascending")]
    )
    if len(np.unique(roads["id"])) != len(roads):
        raise ValueError(f"Duplicate road IDs in {city}")
    shapely.prepare(area)
    edge_sets = []
    for begin in range(0, len(roads), 5000):
        batch = roads.slice(begin, 5000)
        ways = shapely.from_wkb(batch["geometry"].to_pylist())
        # Closed area-tagged ways are represented as polygons in Layercake.
        polygons = shapely.get_type_id(ways) == 3
        if expanded and polygons.any():
            raise ValueError("Unexpected area geometry in expanded frame")
        ways[polygons] = shapely.boundary(ways[polygons])
        if not np.isin(shapely.get_type_id(ways), [1, 5]).all():
            raise ValueError(f"Unexpected highway way geometry in {city}")
        valid = np.flatnonzero(shapely.intersects(area, ways))
        ways = ways[valid]
        cut = ~shapely.covers(area, ways)
        ways[cut] = shapely.intersection(area, ways[cut])
        for lines, ids in line_arrays(ways, begin + valid):
            coords, part = shapely.get_coordinates(lines, return_index=True)
            adjacent = part[:-1] == part[1:]
            a = coords[:-1][adjacent]
            b = coords[1:][adjacent]
            distance = GEOD.inv(a[:, 0], a[:, 1], b[:, 0], b[:, 1])[2]
            edge_sets.append(
                np.column_stack([a, b, distance, ids[part[:-1][adjacent]]])
            )
    if not edge_sets:
        return None, 0, []
    edges = np.concatenate(edge_sets)
    edges = edges[edges[:, 4] > 0]
    if not len(edges):
        return None, 0, []
    ends = np.cumsum(edges[:, 4])
    rng = random.Random(f"{seed}|{key}")
    positions = np.array([rng.random() * ends[-1] for _ in range(count)])
    picks = np.searchsorted(ends, positions, side="right")
    a, b, c, d, length, idx = edges[picks].T
    frac = 1 - (ends[picks] - positions) / length
    rows = roads.take(pa.array(idx.astype("int64"))).drop(["geometry"]).to_pandas()
    rows.rename(
        columns={"id": "osm_id", "highway": "osm_type", "name": "osm_name"},
        inplace=True,
    )
    rows = rows.assign(
        location_id=[f"{'v2_' if expanded else ''}{key}_{j:05d}" for j in range(count)],
        city=city,
        sampling_unit=key,
        lat=b + (d - b) * frac,
        lon=a + (c - a) * frac,
    )
    points = shapely.points(rows.lon, rows.lat)
    if not shapely.covers(area, points).all():
        raise ValueError(f"Point outside boundary of {city}")
    families = []
    if expanded:
        labels = roads.column("route_family").to_numpy()
        totals = pd.Series(edges[:, 4]).groupby(labels[edges[:, 5].astype(int)]).sum()
        counts = rows.route_family.value_counts()
        for family, meters in totals.items():
            families.append(
                {
                    "sampling_unit": key,
                    "route_family": family,
                    "eligible_m": meters,
                    "expected_points": count * meters / ends[-1],
                    "sampled_points": int(counts.get(family, 0)),
                }
            )
    return rows, ends[-1], families


@click.command()
@click.option("--study", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--supplemental-boundaries", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--expanded", is_flag=True, help="Use the pedestrian-accessible route frame."
)
@click.option(
    "--prepare-only", is_flag=True, help="Freeze eligible routes before drawing."
)
def main(study, output, supplemental_boundaries, expanded, prepare_only):
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "road_groups"
    cache.mkdir(exist_ok=True)
    with (study / "outputs/draw/selected_centers.csv").open() as file:
        roster = {"uc" + r["id"]: r for r in csv.DictReader(file)}
    names = {r["id"]: r["name"] for r in roster.values()}
    features = read_centers(study / "inputs/boundaries.jsonl.gz", names.keys())
    cities = {
        f"uc{k}": (names[k], shapely.make_valid(shapely.geometry.shape(f["geometry"])))
        for k, f in features.items()
    }
    with (study / "outputs/draw/selected_prior_sites.csv").open() as file:
        outside = {r["city_id"]: r for r in csv.DictReader(file) if not r["center_id"]}
    additions = (
        json.loads(supplemental_boundaries.read_text())["features"]
        if supplemental_boundaries
        else []
    )
    for feature in additions:
        site = str(feature["properties"]["prior_site_id"])
        row = outside.pop(site)
        area = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        point = shapely.Point(float(row["city_lon"]), float(row["city_lat"]))
        if not area.covers(point):
            raise ValueError(f"Supplemental polygon misses the prior site {site}")
        key = "prior" + site
        cities[key] = (row["city_ascii"], area)
        with (study / "inputs/regions.csv").open() as file:
            regions = {r["iso3"]: r["region"] for r in csv.DictReader(file)}
        with (study / "outputs/draw/manifest.json").open() as file:
            prior_n = json.load(file)["prior"]
        with (study / "inputs/prior.csv").open() as file:
            prior_total = sum(1 for _ in csv.DictReader(file))
        roster[key] = {
            "name": row["city_ascii"],
            "iso3": row["iso3"],
            "region": regions[row["iso3"]],
            "prior_sites": site,
            "p_regional": 0,
            "p_prior": prior_n / prior_total,
            "p_any": prior_n / prior_total,
            "regional": 0,
            "prior": 1,
            "anchor": 0,
            "selected": 1,
        }
    if outside:
        raise ValueError(
            f"Missing supplemental boundaries for prior sites {sorted(outside)}"
        )
    meta, snapshot = load_index(cache)
    groups = city_groups(meta, cities)
    print(f"{len(cities)} cities, {len(groups)} spatial row groups", flush=True)
    # Snapshot and city list identify the checkpoint set.
    config = {
        "cities": {k: hashlib.sha256(v[1].wkb).hexdigest() for k, v in cities.items()},
        "classes": EXPANDED if expanded else CLASSES,
        "seed": 42,
        "points": 1000,
        "etag": snapshot["etag"],
    }
    manifest = output / "config.json"
    if manifest.exists() and json.loads(manifest.read_text()) != config:
        raise ValueError("Inputs changed. Use a new output directory.")
    with atomic_path(manifest) as tmp:
        tmp.write_text(json.dumps(config, indent=2) + "\n")

    def extract(i):
        extract_group(i, groups[i], cities, meta, snapshot, cache, expanded)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for n, _ in enumerate(pool.map(extract, groups), 1):
            if n % 20 == 0:
                print(f"Downloaded {n}/{len(groups)} groups", flush=True)
    prepared = output / "eligible"
    if expanded:
        prepared.mkdir(exist_ok=True)
        exceptions, decisions = [], []
        for key, (_, area) in cities.items():
            files = sorted(cache.glob(f"{key}_*.parquet"))
            if not files:
                continue
            ways = pa.concat_tables([pq.read_table(path) for path in files]).sort_by(
                [("id", "ascending")]
            )
            eligible, links, access = prepare_routes(ways, area)
            with atomic_path(prepared / f"{key}.parquet") as tmp:
                pq.write_table(eligible, tmp)
            exceptions.append(links.assign(sampling_unit=key))
            decisions.append(access.assign(sampling_unit=key))
        pd.concat(exceptions, ignore_index=True).to_csv(
            output / "sidewalk_decisions.csv", index=False
        )
        pd.concat(decisions, ignore_index=True).to_csv(
            output / "access_decisions.csv", index=False
        )
    if prepare_only:
        return
    rows = []
    summary = []
    family_counts = []
    for key, (city, area) in cities.items():
        files = (
            sorted(prepared.glob(f"{key}.parquet"))
            if expanded
            else sorted(cache.glob(f"{key}_*.parquet"))
        )
        data, length, families = draw(key, city, area, files, 1000, 42, expanded)
        family_counts.extend(families)
        if data is not None:
            with atomic_path(output / f"{key}.csv") as tmp:
                data.to_csv(tmp, index=False)
            rows.append(data)
        summary.append(
            {
                **roster[key],
                "sampling_unit": key,
                "city": city,
                "locations": 0 if data is None else len(data),
                "road_length_km": length / 1000,
                "sampled_ways": 0 if data is None else data.osm_id.nunique(),
            }
        )
        print(city, summary[-1]["locations"], flush=True)
    if expanded:
        with atomic_path(output / "family_summary.csv") as tmp:
            pd.DataFrame(family_counts).to_csv(tmp, index=False)
    with atomic_path(output / "locations.csv") as tmp:
        pd.concat(rows, ignore_index=True).to_csv(tmp, index=False)
    with atomic_path(output / "city_summary.csv") as tmp:
        pd.DataFrame(summary).to_csv(tmp, index=False)


if __name__ == "__main__":
    main()
