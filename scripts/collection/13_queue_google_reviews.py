"""Queue downloaded Google views in the local Label Studio installation."""

import argparse
import csv
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

import django
import label_studio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(label_studio.__file__).parent))
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE", "label_studio.core.settings.label_studio"
    )
    django.setup()
    from data_import.models import FileUpload
    from django.conf import settings
    from django.db import transaction
    from projects.models import Project
    from tasks.models import Task

    prior = Project.objects.get(title="missing_women_300")
    config = (args.study / "paper/protocol/label_config.xml").read_text()
    project, _ = Project.objects.get_or_create(
        title="Urban street presence — Google round 1",
        organization=prior.organization,
        defaults={"created_by": prior.created_by, "label_config": config},
    )
    if project.label_config.strip() != config.strip():
        raise ValueError("Existing project uses different labels")
    with (args.study / "outputs/round1/selected_views.csv").open() as stream:
        roster = {r["location_id"]: r for r in csv.DictReader(stream)}
    reserve_queue = args.folder / "november_reserve_queue.csv"
    if reserve_queue.exists():
        with reserve_queue.open() as stream:
            roster.update({r["location_id"]: r for r in csv.DictReader(stream)})
    with (args.study / "outputs/locations_expanded/city_summary.csv").open() as stream:
        cities = {r["sampling_unit"]: r for r in csv.DictReader(stream)}
    db = sqlite3.connect(args.folder / "acquisition.sqlite")
    done = {
        t["location_id"]
        for t in Task.objects.filter(project=project).values_list("data", flat=True)
    }
    for location_id, digest in db.execute(
        "SELECT location_id,sha256 FROM calls WHERE status='ready' ORDER BY rowid"
    ):
        if location_id in done:
            continue
        row = roster[location_id]
        city = cities[row["sampling_unit"]]
        src = args.folder / "images" / (location_id + ".jpg")
        rel = Path(settings.UPLOAD_DIR) / str(project.pk) / (digest + "-" + src.name)
        dst = Path(settings.MEDIA_ROOT) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            os.link(src, dst)
        if hashlib.sha256(dst.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Hash mismatch for {location_id}")
        with transaction.atomic():
            upload, _ = FileUpload.objects.get_or_create(
                project=project, user=prior.created_by, file=str(rel)
            )
            data = {
                "location_id": location_id,
                "city": row["city"],
                "iso3": city["iso3"],
                "region": city["region"],
                "image_sha256": digest,
                "image": upload.url,
            }
            Task.objects.create(project=project, file_upload=upload, data=data)
    print(f"Project {project.pk}, tasks {Task.objects.filter(project=project).count()}")


if __name__ == "__main__":
    main()
