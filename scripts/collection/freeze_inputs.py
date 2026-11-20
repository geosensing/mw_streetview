"""Freeze only the geographic information needed from sibling studies."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("siblings", type=Path)
    root = parser.parse_args().siblings
    files = {}
    out = Path("inputs")
    out.mkdir(exist_ok=True)

    def source(name):
        path = root / name
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    previous = "missing_women_crowdsourced_photos_multicountry/"
    centers = pd.read_csv(source(previous + "data/raw/cities688.csv"))
    selected = pd.read_csv(source(previous + "outputs/selected_cities.csv"))
    sites = selected[["city_id"]].merge(centers, validate="one_to_one")
    if len(sites) != 101:
        raise ValueError("Expected geographic records for all 101 prior sites")
    sites[["city_id", "city_ascii", "iso3", "city_lat", "city_lon"]].to_csv(
        out / "prior.csv", index=False
    )
    countries = json.loads(source(previous + "data/raw/wb_countries.json").read_text())[
        1
    ]
    with (out / "regions.csv").open("w") as file:
        writer = csv.writer(file)
        writer.writerow(["iso3", "region"])
        for country in countries:
            region = country["region"]["value"].strip()
            if region == "Aggregates":
                continue
            if region in ("North America", "Latin America & Caribbean"):
                region = "Americas"
            writer.writerow([country["id"], region])
    tracks = []
    for site in ("mumbai", "navi_mumbai", "delhi", "bangalore"):
        frame = pd.read_parquet(
            source(f"missing_women_bike_camera_india/data/{site}/analysis_data.parquet")
        )
        points = frame.loc[
            frame.gps_valid & ~frame.gps_out_of_bounds, ["gps_lat", "gps_lon"]
        ].dropna()
        tracks.append(
            points.set_axis(["lat", "lon"], axis=1).assign(
                site=site, status="completed"
            )
        )
    planned = pd.read_csv(source("streetsight/data/hcmc/samples/hcmc_field_sample.csv"))
    tracks.append(planned[["lat", "lon"]].assign(site="hcmc", status="planned"))
    pd.concat(tracks).to_csv(out / "bike.csv", index=False)
    (out / "sibling_sha256.json").write_text(json.dumps(files, indent=2) + "\n")


if __name__ == "__main__":
    main()
