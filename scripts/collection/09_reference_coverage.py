"""Find frozen locations near a camera in the pinned Global Streetscapes catalog."""

from pathlib import Path

import click
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.spatial import cKDTree


def sphere(lat, lon):
    y, x = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(y) * np.cos(x), np.cos(y) * np.sin(x), np.sin(y)], axis=1)


@click.command()
@click.argument("ledger", type=click.Path(path_type=Path))
@click.argument("catalog", type=click.Path(path_type=Path))
@click.argument("output", type=click.Path(path_type=Path))
def main(ledger, catalog, output):
    frame = pd.read_csv(ledger, keep_default_na=False)
    if frame.lookup_utc.eq("").any():
        raise click.ClickException("Complete the Google coverage ledger first")
    xyz = sphere(frame.lat.to_numpy(), frame.lon.to_numpy())
    matched = np.zeros(len(frame), dtype=bool)
    chord = 2 * np.sin(25 / 6371000)
    for batch in pq.ParquetFile(catalog).iter_batches(
        batch_size=65536, columns=["lat", "lon"]
    ):
        rows = batch.to_pandas().dropna()
        points = sphere(rows.lat.to_numpy(), rows.lon.to_numpy())
        if not len(points):
            continue
        lo = points.min(axis=0) - chord
        hi = points.max(axis=0) + chord
        ix = np.flatnonzero(~matched & ((xyz >= lo) & (xyz <= hi)).all(axis=1))
        if len(ix):
            distance, _ = cKDTree(points).query(xyz[ix], distance_upper_bound=chord)
            matched[ix[np.isfinite(distance)]] = True
    frame["google_status"] = np.where(frame.pano_id.ne(""), "matched", "no_panorama")
    frame.loc[frame.coverage_state.eq("lookup_error"), "google_status"] = "lookup_error"
    frame["reference_within_50m"] = matched
    frame[
        ["location_id", "sampling_unit", "google_status", "reference_within_50m"]
    ].to_csv(output / "reference_locations.csv", index=False)
    counts = frame.groupby(
        ["sampling_unit", "city", "google_status"]
    ).reference_within_50m.agg(candidates="size", nearby="sum")
    counts.to_csv(output / "reference_by_city.csv")
    by_google = counts.groupby("google_status").nearby.sum()
    with (output / "reference.tex").open("w") as file:
        for name, label in [("RefPresent", "matched"), ("RefMissing", "no_panorama")]:
            file.write(
                r"\newcommand{"
                + chr(92)
                + name
                + "}{"
                + f"{by_google.get(label, 0):,}"
                + "}\n"
            )


if __name__ == "__main__":
    main()
