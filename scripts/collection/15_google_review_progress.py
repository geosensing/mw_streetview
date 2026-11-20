"""Report Google acquisition, human submission, and Muse progress per sampled site."""

import json
import os
import sqlite3
from pathlib import Path

import click
import pandas as pd
from gsview.cities import atomic_path
from gsview.streetview import haversine_m


@click.command()
@click.argument("study", type=click.Path(path_type=Path))
@click.argument("folder", type=click.Path(path_type=Path))
@click.option("--label-db", type=click.Path(path_type=Path), required=True)
@click.option("--project-id", type=int, required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
def main(study, folder, label_db, project_id, output):
    frame = pd.read_csv(
        study / "outputs/round1/location_ledger.csv",
        usecols=[
            "location_id",
            "sampling_unit",
            "city",
            "lat",
            "lon",
            "coverage_state",
        ],
    )
    with sqlite3.connect(
        f"file:{(folder / 'acquisition.sqlite').resolve()}?mode=ro", uri=True
    ) as db:
        calls = pd.read_sql_query("select * from calls", db)
    with sqlite3.connect(f"file:{label_db.resolve()}?mode=ro", uri=True) as db:
        tasks = pd.read_sql_query(
            """select id as task_id,json_extract(data,'$.location_id') as location_id,
                    is_labeled from task where project_id=?""",
            db,
            params=(project_id,),
        )
    frame = frame.merge(
        calls, on="location_id", how="left", validate="one_to_one"
    ).merge(tasks, on="location_id", how="left", validate="one_to_one")
    frame["status"] = frame.status.fillna("not_requested")
    frame["ready"] = frame.status.eq("ready")
    image_dir = Path(os.path.relpath((folder / "images").resolve(), output.resolve()))
    frame["image_file"] = ""
    frame.loc[frame.ready, "image_file"] = frame.loc[frame.ready, "location_id"].map(
        lambda location: str(image_dir / (location + ".jpg"))
    )
    frame["queued"] = frame.task_id.notna()
    frame["submitted"] = frame.is_labeled.eq(1)
    frame["muse_complete"] = False
    counts = folder / "muse_counts.csv"
    if counts.exists():
        records = pd.read_csv(counts)
        frame["muse_complete"] = frame.location_id.isin(
            records.image.map(lambda p: Path(p).stem)
        )
    for idx, meta_text in frame.loc[
        frame.metadata.fillna("").ne(""), "metadata"
    ].items():
        meta = json.loads(meta_text)
        frame.at[idx, "metadata_status"] = meta.get("status")
        frame.at[idx, "capture_date"] = meta.get("date")
        frame.at[idx, "copyright"] = meta.get("copyright")
        latlon = meta.get("location") or {}
        if "lat" in latlon and "lng" in latlon:
            frame.at[idx, "camera_lat"] = latlon["lat"]
            frame.at[idx, "camera_lon"] = latlon["lng"]
            frame.at[idx, "snap_distance_m"] = haversine_m(
                frame.at[idx, "lat"], frame.at[idx, "lon"], latlon["lat"], latlon["lng"]
            )
    frame["batch"] = "round1"
    supplemental = folder / "november_reserve_queue.csv"
    if supplemental.exists():
        reserve_ids = pd.read_csv(supplemental, usecols=["location_id"]).location_id
        frame.loc[frame.location_id.isin(reserve_ids), "batch"] = "november_reserve"
    frame["reserve_images"] = frame.ready & frame.batch.eq("november_reserve")
    frame["round1_images"] = frame.ready & frame.batch.eq("round1")
    frame["requested"] = frame.status.ne("not_requested")
    summary = frame.groupby(["sampling_unit", "city"]).agg(
        sampled=("location_id", "size"),
        requested=("requested", "sum"),
        images=("ready", "sum"),
        round1_images=("round1_images", "sum"),
        reserve_images=("reserve_images", "sum"),
        queued_for_humans=("queued", "sum"),
        human_submitted=("submitted", "sum"),
        muse_counted=("muse_complete", "sum"),
    )
    output.mkdir(parents=True, exist_ok=True)
    with atomic_path(output / "acquisition.csv") as tmp:
        frame[frame.requested].drop(columns=["metadata"]).to_csv(tmp, index=False)
    with atomic_path(output / "review_progress.csv") as tmp:
        summary.to_csv(tmp)
    print(summary.sum().to_dict())


if __name__ == "__main__":
    main()
