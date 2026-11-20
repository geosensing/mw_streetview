"""Export public analysis columns from the original study and image ledgers."""

import argparse
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("streetsight", type=Path)
    args = parser.parse_args()
    o = args.study / "outputs"
    s = args.streetsight
    r = Path(__file__).resolve().parents[1]
    keys = ["location_id", "sampling_unit"]
    ledger = pd.read_csv(o / "round1/location_ledger.csv", keep_default_na=False)
    ref = pd.read_csv(o / "google_selection/reference_locations.csv")
    df = ledger[
        [
            *keys,
            "city",
            "region",
            "rank_in_city",
            "osm_type",
            "route_family",
            "lat",
            "lon",
            "lookup_utc",
            "coverage_state",
        ]
    ].copy()
    codes = pd.factorize(ledger.pano_id.mask(ledger.pano_id.eq("")))[0]
    df["panorama_key"] = pd.array(codes + 1, dtype="Int64")
    df.loc[codes == -1, "panorama_key"] = pd.NA
    df = df.merge(ref[[*keys, "reference_within_50m"]], on=keys, validate="one_to_one")
    assert len(df) == len(ledger) == len(ref)
    df.to_csv(
        r / "data/locations.csv.gz",
        index=False,
        compression={"method": "gzip", "mtime": 0},
    )
    roster = pd.read_csv(o / "locations_expanded/city_summary.csv")
    progress = pd.read_csv(o / "round1/review_progress.csv")
    roster.merge(
        progress[["sampling_unit", "human_submitted"]],
        on="sampling_unit",
        validate="one_to_one",
    ).to_csv(r / "data/cities.csv", index=False)
    source = s / "data/cities/google_images"
    det = pd.read_csv(source / "person_detector/images.csv")
    acq = pd.read_csv(o / "round1/acquisition.csv")
    acq = acq[acq.ready]
    det = det.merge(
        acq[[*keys, "city", "batch", "capture_date"]],
        on=[*keys, "city", "batch"],
        validate="one_to_one",
    )
    muse = pd.read_csv(source / "muse_counts.csv")
    muse["location_id"] = muse.image.map(lambda x: Path(x).stem)
    counts = [
        "women_count",
        "men_count",
        "women_twowheeler",
        "men_twowheeler",
        "unclear_count",
    ]
    assert (
        set(muse.location_id)
        == set(det.loc[det.n_person.ge(1), "location_id"])
        == set(pd.read_csv(source / "person_detector/queue_all.csv").location_id)
    )
    assert len(det) == len(acq) == 9997
    det = det.merge(
        muse[["location_id", *counts]],
        how="left",
        on="location_id",
        validate="one_to_one",
    )
    det["capture_year"] = det.capture_date.str[:4]
    det[counts] = det[counts].astype("Int64")
    det[[*keys, "city", "batch", "capture_year", "n_person", *counts]].to_csv(
        r / "data/images.csv.gz",
        index=False,
        compression={"method": "gzip", "mtime": 0},
    )


if __name__ == "__main__":
    main()
