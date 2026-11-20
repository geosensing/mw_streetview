"""Run the same road sample and panorama lookup for each roster polygon."""

import csv
import gzip
import hashlib
import itertools
import json
import random
from bisect import bisect_right
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

import click
import pandas as pd
from pyproj import Geod
from shapely import make_valid
from shapely.geometry import LineString, shape

from .downloader import check_coverage_batch
from .sampling import _fetch_roads_from_overpass

GEOD = Geod(ellps="WGS84")
FIELDS = [
    "location_id",
    "center_id",
    "city",
    "lat",
    "lon",
    "osm_id",
    "osm_type",
    "osm_name",
]


@contextmanager
def atomic_path(path):
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temp = Path(file.name)
    try:
        yield temp
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def read_centers(boundaries, ids):
    centers = {}
    with gzip.open(boundaries, "rt", encoding="utf8") as file:
        for line in file:
            feature = json.loads(line)
            key = str(feature["properties"]["ID_UC_G0"])
            if key in ids:
                if key in centers:
                    raise click.ClickException(f"Duplicate boundary for center {key}")
                centers[key] = feature
    missing = ids - centers.keys()
    if missing:
        raise click.ClickException(f"Missing boundaries for centers {sorted(missing)}")
    return centers


def line_parts(geometry):
    if geometry.geom_type == "LineString" and not geometry.is_empty:
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from line_parts(part)


def sample_points(roads, boundary, key, city, n, seed):
    # geo-sampling draws segment rows. Here every meter of clipped road has equal weight.
    edges, ends = [], []
    total = 0.0
    for road in sorted(roads, key=lambda row: row["osm_id"]):
        line = LineString([(lon, lat) for lat, lon in road["coords"]])
        for part in line_parts(line.intersection(boundary)):
            coords = list(part.coords)
            for start, end in itertools.pairwise(coords):
                length = GEOD.inv(*start, *end)[2]
                if length > 0:
                    total += length
                    ends.append(total)
                    edges.append((start, end, length, road))
    rng = random.Random(f"{seed}|{key}")
    rows = []
    for index in range(n if edges else 0):
        distance = rng.random() * total
        i = min(bisect_right(ends, distance), len(edges) - 1)
        (x, y), (x2, y2), length, road = edges[i]
        fraction = 1 - (ends[i] - distance) / length
        rows.append(
            [
                f"uc{key}_{index:05d}",
                key,
                city,
                y + (y2 - y) * fraction,
                x + (x2 - x) * fraction,
                road["osm_id"],
                road["highway"],
                road["name"],
            ]
        )
    return pd.DataFrame(rows, columns=FIELDS), total


def sample_center(folder, feature, n, seed):
    key = str(feature["properties"]["ID_UC_G0"])
    city = feature["properties"]["GC_UCN_MAI_2025"]
    boundary = make_valid(shape(feature["geometry"]))
    config = {
        "center_id": key,
        "seed": seed,
        "requested": n,
        "design": "road-length-v1",
        "boundary_sha256": hashlib.sha256(boundary.wkb).hexdigest(),
    }
    manifest = folder / "manifest.json"
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if any(previous[k] != v for k, v in config.items()):
            raise click.ClickException(
                f"Changed sample settings for {key}; choose another --output"
            )
    else:
        with atomic_path(manifest) as tmp:
            tmp.write_text(json.dumps(config, indent=2))
    roads_path = folder / "roads.json.gz"
    locations = folder / "locations.csv"
    previous = json.loads(manifest.read_text())
    if "locations_sha256" in previous:
        if not locations.exists():
            raise click.ClickException(f"Missing frozen sample: {locations}")
        digest = hashlib.sha256(locations.read_bytes()).hexdigest()
        if digest != previous["locations_sha256"]:
            raise click.ClickException(f"Sample checksum mismatch in {locations}")
        return pd.read_csv(locations)
    if not roads_path.exists():
        west, south, east, north = boundary.bounds
        roads = _fetch_roads_from_overpass(bbox=(south, west, north, east))
        with atomic_path(roads_path) as tmp, gzip.open(
            tmp, "wt", encoding="utf8"
        ) as file:
            json.dump(roads, file, separators=(",", ":"))
    with gzip.open(roads_path, "rt") as file:
        rows, total = sample_points(json.load(file), boundary, key, city, n, seed)
    with atomic_path(locations) as tmp:
        rows.to_csv(tmp, index=False)
    config.update(
        road_length_m=total,
        sampled=len(rows),
        roads_sha256=hashlib.sha256(roads_path.read_bytes()).hexdigest(),
        locations_sha256=hashlib.sha256(locations.read_bytes()).hexdigest(),
    )
    with atomic_path(manifest) as tmp:
        tmp.write_text(json.dumps(config, indent=2) + "\n")
    return pd.read_csv(locations)


def lookup(folder, locations):
    parts = []
    cache = folder / "metadata"
    cache.mkdir(exist_ok=True)
    for start in range(0, len(locations), 25):
        stop = start + 25
        chunk = locations.iloc[start:stop]
        path = cache / f"{start:06d}.csv"
        if not path.exists():
            with atomic_path(path) as tmp:
                check_coverage_batch(chunk, output_path=tmp)
        part = pd.read_csv(path, dtype={"pano_id": str, "capture_date": str})
        if part.location_id.tolist() != chunk.location_id.tolist():
            raise click.ClickException(f"Coverage checkpoint does not match {path}")
        parts.append(part)
    columns = [
        "location_id",
        "has_coverage",
        "pano_id",
        "capture_date",
        "pano_lat",
        "pano_lon",
        "snap_distance_m",
        "status",
    ]
    hits = pd.concat(parts)[columns] if parts else pd.DataFrame(columns=columns)
    with atomic_path(folder / "coverage.csv") as tmp:
        locations.merge(hits, on="location_id", validate="one_to_one").to_csv(
            tmp, index=False
        )


@click.command("cities")
@click.option(
    "--boundaries",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Frozen GHSL boundaries.jsonl.gz from the city sampler.",
)
@click.option(
    "--roster",
    type=click.Path(exists=True, path_type=Path),
    help="Draw CSV with id and selected columns.",
)
@click.option(
    "--center",
    multiple=True,
    type=click.IntRange(min=1),
    help="Process a single GHSL center ID; repeat for several.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default="data/cities",
    show_default=True,
)
@click.option("--points", type=click.IntRange(min=1), default=1000, show_default=True)
@click.option("--seed", type=int, default=42, show_default=True)
@click.option(
    "--lookup", "with_lookup", is_flag=True, help="Also query panorama metadata."
)
def cities(boundaries, roster, center, output, points, seed, with_lookup):
    """Sample roads within city polygons and optionally look up panoramas."""
    import fcntl

    if bool(roster) == bool(center):
        raise click.UsageError("Provide either --roster or --center.")
    ids = {str(i) for i in center}
    if roster:
        with roster.open() as file:
            reader = csv.DictReader(file)
            if not {"id", "selected"} <= set(reader.fieldnames or []):
                raise click.UsageError(
                    "Use a draw roster with id and selected columns."
                )
            for row in reader:
                if row["selected"] not in {"0", "1"}:
                    raise click.UsageError(
                        "Expected binary selected flags in the draw roster."
                    )
                if row["selected"] == "1":
                    ids.add(row["id"])
    if not ids or not all(i.isascii() and i.isdecimal() for i in ids):
        raise click.UsageError("Expected one or more numeric GHSL center IDs.")
    for key, feature in sorted(read_centers(boundaries, ids).items()):
        folder = output / key
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            locations = sample_center(folder, feature, points, seed)
            click.echo(f"{key}: {len(locations)} sampled locations in {folder}")
            if with_lookup:
                lookup(folder, locations)
