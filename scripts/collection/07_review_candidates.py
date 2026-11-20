"""Rank frozen locations and check round-one capture years without downloading images."""

import hashlib
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import click
import pandas as pd
import requests
from gsview.cities import atomic_path
from gsview.streetview import METADATA_URL, StreetViewClient
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

COUNT_COLUMNS = [
    "region",
    "city",
    "sampling_unit",
    "status",
    "capture_year",
    "date_precision",
]
ID_COLUMNS = ["location_id", "pano_id", "lookup_utc"]


@click.command()
@click.option("--source", type=click.Path(path_type=Path), required=True)
@click.option(
    "--output", type=click.Path(path_type=Path), default=Path("data/cities/round1")
)
@click.option(
    "--reserves", is_flag=True, help="Continue to 100 matched locations per site."
)
@click.option(
    "--all-locations", is_flag=True, help="Measure coverage at every frozen point."
)
def main(source, output, reserves, all_locations):
    frame = pd.read_csv(source / "locations.csv").merge(
        pd.read_csv(source / "city_summary.csv", usecols=["sampling_unit", "region"]),
        on="sampling_unit",
        validate="many_to_one",
    )
    if (
        not frame.location_id.is_unique
        or not frame.groupby("sampling_unit").size().eq(1000).all()
    ):
        raise click.ClickException(
            "Expected unique location IDs and 1,000 locations per city"
        )
    frame["rank_key"] = frame.location_id.map(
        lambda s: hashlib.sha256(f"round1|42|{s}".encode()).hexdigest()
    )
    frame = frame.sort_values(["sampling_unit", "rank_key"]).drop(columns="rank_key")
    frame["rank_in_city"] = frame.groupby("sampling_unit").cumcount() + 1
    candidates = frame[frame.rank_in_city <= 100]
    output.mkdir(exist_ok=True, parents=True)
    manifest = output / "candidates.csv"
    with atomic_path(manifest) as tmp:
        candidates.to_csv(tmp, index=False)
        if manifest.exists() and tmp.read_bytes() != manifest.read_bytes():
            raise click.ClickException(
                "Different candidate frame. Choose another output directory."
            )
    key = StreetViewClient().api_key
    local = threading.local()

    def lookup(row):
        for attempt in range(4):
            result = request_once(row)
            if result is not None:
                return result
            time.sleep(2**attempt)
        if all_locations:
            return (row.location_id, "", datetime.now(UTC).isoformat()), (
                row.region,
                row.city,
                row.sampling_unit,
                "UNKNOWN_ERROR",
                "",
                "missing",
            )
        raise click.ClickException(
            f"Google returned UNKNOWN_ERROR for {row.location_id}"
        )

    def request_once(row):
        if not hasattr(local, "http"):
            local.http = requests.Session()
            retry = Retry(
                total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504]
            )
            local.http.mount("https://", HTTPAdapter(max_retries=retry))
        try:
            reply = local.http.get(
                METADATA_URL,
                params={"key": key, "location": f"{row.lat},{row.lon}"},
                timeout=15,
            )
            reply.raise_for_status()
            data = reply.json()
        except (requests.RequestException, ValueError) as exc:
            raise click.ClickException(type(exc).__name__) from None
        status = data.get("status")
        if status == "UNKNOWN_ERROR":
            return None
        if status not in {"OK", "ZERO_RESULTS", "NOT_FOUND"} or (
            status == "OK" and not data.get("pano_id")
        ):
            raise click.ClickException(f"Invalid metadata response ({status})")
        date = data.get("date", "")
        record = (
            row.location_id,
            data.get("pano_id", ""),
            datetime.now(UTC).isoformat(),
        )
        precision = {4: "year", 7: "month"}.get(len(date), "missing")
        return record, (
            row.region,
            row.city,
            row.sampling_unit,
            status,
            date[:4],
            precision,
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        for unit, group in candidates.groupby("sampling_unit"):
            ids = output / f"{unit}-ids.csv"
            counts = output / f"{unit}-counts.csv"
            if ids.exists() and counts.exists():
                continue
            categories = Counter()
            rows = []
            for record, category in pool.map(lookup, group.itertuples()):
                categories[category] += 1
                rows.append(record)
            with atomic_path(ids) as tmp:
                pd.DataFrame(rows, columns=ID_COLUMNS).to_csv(tmp, index=False)
            with atomic_path(counts) as tmp:
                pd.DataFrame(
                    [(*k, v) for k, v in categories.items()],
                    columns=[*COUNT_COLUMNS, "locations"],
                ).to_csv(tmp, index=False)
    for suffix, name in [("ids", "panorama_ids"), ("counts", "coverage_by_year")]:
        paths = [
            output / f"{unit}-{suffix}.csv"
            for unit in sorted(candidates.sampling_unit.unique())
        ]
        with atomic_path(output / f"{name}.csv") as tmp:
            pd.concat(
                [pd.read_csv(p, keep_default_na=False) for p in paths],
                ignore_index=True,
            ).to_csv(tmp, index=False)

    if reserves or all_locations:
        fill_reserves(frame, output, lookup, all_locations)


def fill_reserves(frame, output, lookup, all_locations=False):
    initial = pd.read_csv(output / "panorama_ids.csv", keep_default_na=False)
    cache = output / "reserves"
    cache.mkdir(exist_ok=True)
    parts = [initial]
    with ThreadPoolExecutor(max_workers=24) as pool:
        for unit, group in frame.groupby("sampling_unit"):
            pilot = initial[initial.location_id.isin(group.location_id)]
            matched = pilot.pano_id.ne("").sum()
            for start in range(100, len(group), 50):
                if matched >= 100 and not all_locations:
                    break
                stop = start + 50
                batch = group.iloc[start:stop]
                checkpoint = cache / f"{unit}-{start}.csv"
                if checkpoint.exists():
                    hits = pd.read_csv(checkpoint, keep_default_na=False)
                    if hits.location_id.tolist() != batch.location_id.tolist():
                        raise click.ClickException(
                            f"Unexpected checkpoint {checkpoint}"
                        )
                else:
                    hits = pd.DataFrame(
                        [
                            (*record, category[3])
                            for record, category in pool.map(lookup, batch.itertuples())
                        ],
                        columns=[*ID_COLUMNS, "lookup_status"],
                    )
                    with atomic_path(checkpoint) as tmp:
                        hits.to_csv(tmp, index=False)
                parts.append(hits)
                matched += hits.pano_id.ne("").sum()
            click.echo(f"{unit} {min(matched, 100)}")
    ledger = frame.merge(
        pd.concat(parts), on="location_id", how="left", validate="one_to_one"
    )
    available = ledger.pano_id.fillna("").ne("")
    ordinal = available.groupby(ledger.sampling_unit).cumsum()
    ledger["coverage_state"] = "unchecked"
    ledger.loc[ledger.lookup_utc.notna(), "coverage_state"] = "no_panorama"
    if "lookup_status" in ledger:
        ledger.loc[ledger.lookup_status.eq("UNKNOWN_ERROR"), "coverage_state"] = (
            "lookup_error"
        )
    ledger.loc[available, "coverage_state"] = "matched_reserve"
    ledger.loc[available & ordinal.le(100), "coverage_state"] = "selected"
    ledger["image_state"] = "not_requested"
    with atomic_path(output / "location_ledger.csv") as tmp:
        ledger.to_csv(tmp, index=False)
    with atomic_path(output / "city_progress.csv") as tmp:
        pd.crosstab([ledger.sampling_unit, ledger.city], ledger.coverage_state).to_csv(
            tmp
        )

    selected = ledger.loc[
        ledger.coverage_state.eq("selected"),
        ["location_id", "city", "sampling_unit", "rank_in_city", "pano_id"],
    ].copy()
    selected["preferred_year"] = 2026
    selected["heading"] = selected.location_id.map(
        lambda value: int(
            hashlib.sha256(f"heading|42|{value}".encode()).hexdigest(), 16
        )
        % 360
    )
    selected["pitch"] = 0
    selected["fov"] = 90
    selected["maps_url"] = [
        "https://www.google.com/maps/@?"
        + urlencode(
            {
                "api": 1,
                "map_action": "pano",
                "pano": row.pano_id,
                "heading": row.heading,
                "pitch": row.pitch,
                "fov": row.fov,
            }
        )
        for row in selected.itertuples()
    ]
    with atomic_path(output / "selected_views.csv") as tmp:
        selected.to_csv(tmp, index=False)


if __name__ == "__main__":
    main()
