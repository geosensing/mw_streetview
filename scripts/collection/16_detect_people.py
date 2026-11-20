# /// script
# requires-python = ">=3.11"
# dependencies = ["ultralytics==8.4.215"]
# ///
"""Screen verified Street View images for Muse. Omit --limit for the full corpus."""

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from ultralytics import YOLO

WEIGHTS_SHA = "85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    folder = args.output
    weights = folder / "yolo11s.pt"
    if digest(weights) != WEIGHTS_SHA:
        raise ValueError("Unexpected YOLO11s weights")
    model = YOLO(str(weights))
    if model.names[0] != "person":
        raise ValueError("Expected COCO class 0 to be person")
    with args.manifest.open() as stream:
        rows = [r for r in csv.DictReader(stream) if r["ready"] == "True"]
    rows.sort(
        key=lambda r: (
            r["is_labeled"] != "1.0",
            hashlib.sha256(("pilot-v1/" + r["location_id"]).encode()).digest(),
        )
    )
    if len({r["location_id"] for r in rows}) != len(rows):
        raise ValueError("Repeated location in acquisition manifest")
    rows = rows[: args.limit]
    with sqlite3.connect(folder / "detections.sqlite") as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS detections "
            "(location_id TEXT PRIMARY KEY, sha256 TEXT, n_person INTEGER, boxes TEXT)"
        )
        for row in rows:
            loc = row["location_id"]
            path = args.manifest.parent / row["image_file"]
            if digest(path) != row["sha256"]:
                raise ValueError(f"Image hash mismatch for {loc}")
            old = db.execute(
                "SELECT sha256 FROM detections WHERE location_id=?", (loc,)
            ).fetchone()
            if old == (row["sha256"],):
                continue
            if old:
                raise ValueError(f"Cached image differs for {loc}")
            result = model.predict(
                str(path),
                classes=[0],
                conf=0.15,
                iou=0.7,
                imgsz=640,
                device="cpu",
                verbose=False,
            )[0]
            boxes = [
                {"xyxy": xy, "score": score}
                for xy, score in zip(
                    result.boxes.xyxy.tolist(), result.boxes.conf.tolist()
                )
            ]
            with db:
                db.execute(
                    "INSERT INTO detections VALUES (?,?,?,?)",
                    (loc, row["sha256"], len(boxes), json.dumps(boxes)),
                )
        if args.limit is None:
            results = dict(db.execute("SELECT location_id, n_person FROM detections"))
            with (folder / "images.csv").open("w", newline="") as stream:
                fields = [
                    "location_id",
                    "city",
                    "sampling_unit",
                    "batch",
                    "sha256",
                    "n_person",
                ]
                writer = csv.DictWriter(
                    stream, fieldnames=fields, extrasaction="ignore"
                )
                writer.writeheader()
                for row in rows:
                    writer.writerow(dict(row, n_person=results[row["location_id"]]))
            by_city = Counter((r["city"], results[r["location_id"]] > 0) for r in rows)
            with (folder / "cities.csv").open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    ["city", "images_screened", "images_positive", "images_negative"]
                )
                for city in sorted({r["city"] for r in rows}):
                    positive = by_city[city, True]
                    negative = by_city[city, False]
                    writer.writerow([city, positive + negative, positive, negative])
        print(
            db.execute(
                "SELECT count(*), sum(n_person>0), sum(n_person) FROM detections"
            ).fetchone()
        )


if __name__ == "__main__":
    main()
