"""Show current metadata for the exact round-one panorama at a sampled location."""

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import click
import requests
from gsview.streetview import METADATA_URL, StreetViewClient, haversine_m


@click.command()
@click.argument("location_id")
@click.option(
    "--folder", type=click.Path(path_type=Path), default=Path("data/cities/round1")
)
def main(location_id, folder):
    def find_row(file):
        with (folder / file).open(newline="") as stream:
            row = next(
                (r for r in csv.DictReader(stream) if r["location_id"] == location_id),
                None,
            )
        if row is None:
            raise click.ClickException(f"Unknown location in {file}")
        return row

    if (folder / "location_ledger.csv").exists():
        candidate = find_row("location_ledger.csv")
        if candidate["coverage_state"] == "unchecked":
            raise click.ClickException("This location has not been queried")
        hit = {k: candidate[k] for k in ("location_id", "pano_id", "lookup_utc")}
    else:
        candidate = find_row("candidates.csv")
        hit = find_row("panorama_ids.csv")
    if not hit["pano_id"]:
        raise click.ClickException("No panorama returned in the coordinate lookup")
    try:
        response = requests.get(
            METADATA_URL,
            params={"key": StreetViewClient().api_key, "pano": hit["pano_id"]},
            timeout=20,
        )
        response.raise_for_status()
        metadata = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise click.ClickException(type(exc).__name__) from None
    result = {
        "sample": candidate,
        "original_lookup": hit,
        "checked_utc": datetime.now(UTC).isoformat(),
        "provider_metadata": metadata,
    }
    if metadata.get("status") == "OK":
        camera = metadata["location"]
        result["snap_distance_m"] = round(
            haversine_m(
                float(candidate["lat"]),
                float(candidate["lon"]),
                camera["lat"],
                camera["lng"],
            ),
            2,
        )
        result["capture_date_precision"] = {4: "year", 7: "month"}.get(
            len(metadata.get("date", "")), "unknown"
        )
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
