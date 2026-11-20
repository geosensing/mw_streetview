"""Run the frozen prior-study Muse protocol on verified detector-positive Google images."""

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("folder", type=Path)
    parser.add_argument(
        "--limit", type=int, help="Maximum images (default: all detector positives)"
    )
    parser.add_argument("--cap-usd", type=float)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if (args.limit is not None and args.limit < 1) or (
        not args.prepare_only and (args.cap_usd is None or args.cap_usd <= 0)
    ):
        parser.error("Provide a positive image limit and USD budget")
    if args.workers < 1:
        parser.error("--workers must be positive")
    study = args.study.resolve()
    folder = args.folder.resolve()
    protocol = study / "paper/protocol"
    runner = (
        study.parent
        / "missing_women_crowdsourced_photos_multicountry/scripts/05_muse_count.py"
    )
    expected = json.loads((protocol / "source_hashes.json").read_text())[runner.name]
    if hashlib.sha256(runner.read_bytes()).hexdigest() != expected:
        raise ValueError("Upstream runner differs from the frozen protocol")
    detector = folder / "person_detector/detections.sqlite"
    with sqlite3.connect(f"file:{detector}?mode=ro", uri=True) as db:
        db.execute(
            "ATTACH DATABASE ? AS acquisition",
            (f"file:{folder}/acquisition.sqlite?mode=ro",),
        )
        missing = db.execute(
            "SELECT count(*) FROM acquisition.calls c LEFT JOIN detections d "
            "ON c.location_id=d.location_id AND c.sha256=d.sha256 "
            "WHERE c.status='ready' AND d.location_id IS NULL"
        ).fetchone()[0]
        if missing:
            raise ValueError(f"{missing} verified images still need detection")
        records = db.execute(
            "SELECT d.location_id,d.sha256,d.n_person FROM detections d JOIN acquisition.calls c "
            "ON c.location_id=d.location_id AND c.sha256=d.sha256 "
            "WHERE c.status='ready' AND d.n_person >= 1"
        ).fetchall()
    with (study / "outputs/round1/acquisition.csv").open() as stream:
        reviewed = {
            r["location_id"] for r in csv.DictReader(stream) if r["is_labeled"] == "1.0"
        }
    records.sort(
        key=lambda r: (r[0] not in reviewed, hashlib.sha256(r[0].encode()).digest())
    )
    records = records[: args.limit]
    if not records:
        raise ValueError("No detector-positive images available")
    dest = (
        folder
        / "person_detector"
        / ("queue_all" if args.limit is None else f"queue_{args.limit}")
    )
    dest.mkdir(exist_ok=True)
    for loc, digest, _ in records:
        src = folder / "images" / (loc + ".jpg")
        target = dest / src.name
        if not target.exists():
            os.link(src, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Changed image for {loc}")
    if {p.stem for p in dest.glob("*.jpg")} != {r[0] for r in records}:
        raise ValueError(
            "The staged directory contains images outside the requested batch"
        )
    with dest.with_suffix(".csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["location_id", "sha256", "n_person"])
        writer.writerows(records)
    if not args.prepare_only:
        subprocess.run(
            [
                sys.executable,
                str(runner),
                "--images-dir",
                str(dest),
                "--limit",
                "0",
                "--grid",
                "1x1",
                "--workers",
                str(args.workers),
                "--reasoning-effort",
                "low",
                "--max-tokens",
                "4000",
                "--cap-usd",
                str(args.cap_usd),
                "--workdir",
                str(folder / "muse_cache"),
                "--out",
                str(folder / "muse_counts.csv"),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
