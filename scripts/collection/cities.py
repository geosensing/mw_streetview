"""Build the city sampling frame, review allocation, and run the lottery."""

import argparse
import csv
import gzip
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from sampling import design
from shapely import Point, STRtree
from shapely.geometry import shape


def read_csv(path):
    with path.open() as file:
        return list(csv.DictReader(file))


def write_csv(path, rows):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(file.name, path)


def readable(name):
    # The GeoPackage contains some UTF-8 names decoded as Latin-1 by its producer.
    try:
        return name.encode("latin1").decode("utf8")
    except (UnicodeError, AttributeError):
        return name


def build_frame(boundaries):
    countries = {r["ghsl_country"]: r for r in read_csv(Path("inputs/countries.csv"))}
    frame, polygons = [], []
    with gzip.open(boundaries, "rt") as file:
        for line in file:
            feature = json.loads(line)
            props = feature["properties"]
            country = countries[props["GC_CNT_UNN_2025"]]
            frame.append(
                dict(
                    id=str(props["ID_UC_G0"]),
                    name=readable(props["GC_UCN_MAI_2025"]),
                    iso3=country["iso3"],
                    region=country["region"],
                    population_2025=props["GC_POP_TOT_2025"],
                )
            )
            polygons.append(shape(feature["geometry"]))
    if len({r["id"] for r in frame}) != 11422 or len(frame) != 11422:
        raise ValueError("Unexpected version or duplicate urban center IDs")
    tree = STRtree(polygons)

    def match(lon, lat, iso):
        hits = tree.query(Point(float(lon), float(lat)), predicate="intersects")
        ids = [frame[i]["id"] for i in hits if frame[i]["iso3"] == iso]
        if len(ids) > 1:
            raise ValueError(f"Ambiguous city match at {lon}, {lat}: {ids}")
        return ids[0] if ids else ""

    return frame, match


def run(args):
    boundaries = Path("inputs/boundaries.jsonl.gz")
    frame, match = build_frame(boundaries)
    prior = read_csv(Path("inputs/prior.csv"))
    for row in prior:
        row["center_id"] = match(
            row["city_lon"],
            row["city_lat"],
            {"HKG": "CHN"}.get(row["iso3"], row["iso3"]),
        )
    matches = {r["city_id"]: r["center_id"] for r in prior}
    if len(matches) != 101 or len(prior) != 101:
        raise ValueError("Expected 101 distinct prior-study site IDs")
    counts = defaultdict(Counter)
    for point in read_csv(Path("inputs/bike.csv")):
        site, status = point["site"], point["status"]
        center = match(point["lon"], point["lat"], "VNM" if site == "hcmc" else "IND")
        counts[site, status][center] += 1

    anchors = defaultdict(list)
    bikes = []
    for (site, status), hits in sorted(counts.items()):
        # Preserve all observed intersections; use the majority center as the city anchor.
        center, n = hits.most_common(1)[0]
        if not center or n * 2 <= hits.total():
            raise ValueError(f"Review anchor assignment for {site}: {hits}")
        anchors[center].append(f"{site} ({status})")
        for key, count in hits.most_common():
            bikes.append(
                dict(
                    site=site,
                    status=status,
                    center_id=key,
                    points=count,
                    total_points=hits.total(),
                    primary=int(key == center),
                )
            )
    labels = {key: "|".join(sites) for key, sites in anchors.items()}
    seed = args.seed if args.action == "draw" else None
    roster, summary, selected_prior = design(
        frame, matches, labels, args.regional, args.prior, seed
    )
    regions = {r["iso3"]: r["region"] for r in read_csv(Path("inputs/regions.csv"))}
    prior_regions = Counter(regions[r["iso3"]] for r in prior)
    for r in summary:
        r["prior_sites_total"] = prior_regions[r["region"]]
        r["expected_prior_draw"] = round(
            r["prior_sites_total"] * args.prior / len(prior), 2
        )
    if len(summary) != 6:
        raise ValueError("Expected six regions")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        for r in prior:
            r["selected"] = int(r["city_id"] in selected_prior)
        write_csv(out / "selected_centers.csv", [r for r in roster if r["selected"]])
        write_csv(out / "selected_prior_sites.csv", [r for r in prior if r["selected"]])
    write_csv(out / "city_frame.csv", roster)
    write_csv(out / "regional_review.csv", summary)
    write_csv(out / "prior_crosswalk.csv", prior)
    write_csv(out / "bike_crosswalk.csv", bikes)
    files = [boundaries, *sorted(Path("inputs").glob("*.csv"))]
    manifest = dict(
        release="GHS-UCDB R2024A V1.2",
        regional=args.regional,
        prior=args.prior,
        action=args.action,
        seed=seed,
        inputs={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["review", "draw"])
    parser.add_argument("--regional", type=int, default=75)
    parser.add_argument("--prior", type=int, default=20)
    parser.add_argument("--seed", default="20261118")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    args.out = args.out or Path("outputs") / args.action
    run(args)
