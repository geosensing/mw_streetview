"""Fetch the frozen Google review views with resumable request and file accounting."""

import hashlib
import io
import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import click
import pandas as pd
import requests
from gsview.cities import atomic_path
from gsview.streetview import IMAGE_URL, METADATA_URL, StreetViewClient
from PIL import Image


def fetch(row, folder, key, coordinate=None):
    params = {
        "pano": row.pano_id,
        "heading": row.heading,
        "pitch": row.pitch,
        "fov": row.fov,
        "size": "640x640",
        "return_error_code": "true",
        "key": key,
    }
    try:
        if coordinate is not None:
            # Some contributor panoramas resolve by coordinate only. Keep the frozen view.
            probe = requests.get(
                METADATA_URL, params={"location": coordinate, "key": key}, timeout=15
            )
            if probe.json().get("pano_id") != row.pano_id:
                return ("panorama_changed", "", 0, "")
            del params["pano"]
            params["location"] = coordinate
        response = requests.get(IMAGE_URL, params=params, timeout=30)
        if response.status_code != 200:
            return ("http_" + str(response.status_code), "", 0, "")
        content = response.content
        with Image.open(io.BytesIO(content)) as im:
            im.load()
            if im.format != "JPEG" or im.size != (640, 640):
                return ("invalid_image", "", 0, "")
        with atomic_path(folder / "images" / (row.location_id + ".jpg")) as tmp:
            tmp.write_bytes(content)
    except (OSError, ValueError, requests.RequestException) as exc:
        return (type(exc).__name__, "", 0, "")
    try:
        meta = requests.get(
            METADATA_URL, params={"pano": row.pano_id, "key": key}, timeout=10
        ).json()
        if coordinate is not None:
            meta["image_request_location"] = coordinate
        metadata = json.dumps(
            {
                k: meta[k]
                for k in (
                    "status",
                    "date",
                    "copyright",
                    "location",
                    "pano_id",
                    "image_request_location",
                )
                if k in meta
            }
        )
    except (ValueError, requests.RequestException):
        metadata = ""
    return ("ready", hashlib.sha256(content).hexdigest(), len(content), metadata)


@click.command()
@click.argument("study", type=click.Path(path_type=Path))
@click.argument("folder", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1, max=9800), required=True)
@click.option("--workers", type=click.IntRange(1, 32), default=6)
@click.option(
    "--round-robin-reserves",
    is_flag=True,
    help="Fetch new matched locations in city rotation.",
)
@click.option("--request-cap", type=click.IntRange(1, 10000), default=9800)
def main(study, folder, limit, workers, round_robin_reserves, request_cap):
    roster = pd.read_csv(study / "outputs/round1/selected_views.csv")
    roster["slot"] = roster.groupby("sampling_unit").cumcount()
    if round_robin_reserves:
        ledger = pd.read_csv(
            study / "outputs/round1/location_ledger.csv",
            usecols=[
                "coverage_state",
                "location_id",
                "city",
                "sampling_unit",
                "rank_in_city",
                "pano_id",
            ],
        )
        reserves = ledger.loc[
            ledger.coverage_state.eq("matched_reserve"),
            ["location_id", "city", "sampling_unit", "rank_in_city", "pano_id"],
        ].sort_values(["sampling_unit", "rank_in_city"])
        reserves["heading"] = reserves.location_id.map(
            lambda value: int(
                hashlib.sha256(f"heading|42|{value}".encode()).hexdigest(), 16
            )
            % 360
        )
        reserves["pitch"] = 0
        reserves["fov"] = 90
        keys = ["pano_id", "heading", "pitch", "fov"]
        reserves = reserves.drop_duplicates(keys)
        used = pd.MultiIndex.from_frame(roster[keys])
        roster = reserves.loc[
            ~pd.MultiIndex.from_frame(reserves[keys]).isin(used)
        ].copy()
        roster["slot"] = roster.groupby("sampling_unit").cumcount()
    roster = roster.sort_values(["slot", "sampling_unit"]).head(limit)
    if round_robin_reserves:
        queue = folder / "november_reserve_queue.csv"
        with atomic_path(queue) as tmp:
            roster.to_csv(tmp, index=False)
            if queue.exists() and tmp.read_bytes() != queue.read_bytes():
                raise click.ClickException(
                    "Reserve roster differs from the frozen queue"
                )
    (folder / "images").mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "acquisition.sqlite")
    schema = """CREATE TABLE IF NOT EXISTS calls
       (location_id TEXT PRIMARY KEY, pano_id TEXT, heading INTEGER, pitch INTEGER, fov INTEGER,
        issued_utc TEXT, status TEXT, sha256 TEXT, bytes INTEGER, metadata TEXT)"""
    db.execute(schema)
    db.execute(
        "CREATE TABLE IF NOT EXISTS past_attempts AS SELECT * FROM calls WHERE 0"
    )
    retryable = {"OSError", "ConnectionError", "ReadTimeout", "http_500", "http_503"}
    coordinates = pd.read_csv(
        study / "outputs/round1/location_ledger.csv",
        usecols=["location_id", "lat", "lon"],
    ).set_index("location_id")
    retryable.add("http_404")
    key = StreetViewClient().api_key
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(roster), workers):
            todo = []
            stop = start + workers
            if shutil.disk_usage(folder).free < 96 * 1024**2:
                raise click.ClickException(
                    "Low disk space; completed files and ledger are saved"
                )
            for row in roster.iloc[start:stop].itertuples():
                prior = db.execute(
                    "SELECT status FROM calls WHERE location_id=?", (row.location_id,)
                ).fetchone()
                if prior and (
                    round_robin_reserves
                    or prior[0] not in retryable
                    or db.execute(
                        "SELECT 1 FROM past_attempts WHERE location_id=?",
                        (row.location_id,),
                    ).fetchone()
                ):
                    continue
                if (
                    db.execute(
                        "SELECT (SELECT COUNT(*) FROM calls) + (SELECT COUNT(*) FROM past_attempts)"
                    ).fetchone()[0]
                    >= request_cap
                ):
                    raise click.ClickException("Reached the request ceiling")
                if prior:
                    db.execute(
                        "INSERT INTO past_attempts SELECT * FROM calls WHERE location_id=?",
                        (row.location_id,),
                    )
                    db.execute(
                        "DELETE FROM calls WHERE location_id=?", (row.location_id,)
                    )
                db.execute(
                    "INSERT INTO calls VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL)",
                    (
                        row.location_id,
                        row.pano_id,
                        row.heading,
                        row.pitch,
                        row.fov,
                        datetime.now(UTC).isoformat(),
                        "issued",
                    ),
                )
                db.commit()
                coordinate = None
                if prior and prior[0] == "http_404":
                    point = coordinates.loc[row.location_id]
                    coordinate = f"{point.lat},{point.lon}"
                todo.append((row, pool.submit(fetch, row, folder, key, coordinate)))
            statuses = []
            for row, future in todo:
                status, digest, n, meta = future.result()
                db.execute(
                    "UPDATE calls SET status=?,sha256=?,bytes=?,metadata=? WHERE location_id=?",
                    (status, digest, n, meta, row.location_id),
                )
                db.commit()
                statuses.append(status)
            if any(
                s not in {"ready", "http_404", "panorama_changed"} for s in statuses
            ):
                raise click.ClickException(str(statuses))
    print(
        db.execute(
            "SELECT status,count(*),sum(bytes) FROM calls GROUP BY status"
        ).fetchall()
    )


if __name__ == "__main__":
    main()
