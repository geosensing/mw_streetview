"""Rebuild the Google coverage exhibits from a completed location ledger."""

from pathlib import Path

import click
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import t

FAMILIES = {
    "street": "Ordinary street",
    "walking_route": "Walking route",
    "service_road": "Service road",
    "pedestrian_or_shared_street": "Pedestrian or shared street",
    "connector_or_motorway": "Connector or motorway",
}
REGION_NAMES = {
    "Middle East, North Africa, Afghanistan & Pakistan": "MENA, Afghanistan & Pakistan"
}


def main():
    output = Path("tabs")
    frame = pd.read_csv("data/locations.csv.gz", keep_default_na=False)
    cities = pd.read_csv(
        "data/cities.csv", usecols=["sampling_unit", "regional", "region"]
    )
    df = frame.merge(cities, on=["sampling_unit", "region"], validate="many_to_one")
    if (
        len(df) != len(frame)
        or set(df.sampling_unit) != set(cities.sampling_unit)
        or not df.location_id.is_unique
        or not df.groupby("sampling_unit").size().eq(1000).all()
        or df.lookup_utc.eq("").any()
        or df.coverage_state.eq("unchecked").any()
    ):
        raise click.ClickException(
            "Coverage analysis requires completed lookups at every frozen location"
        )
    df["covered"] = df.panorama_key.ne("")
    assert df.covered.eq(df.coverage_state.isin(["selected", "matched_reserve"])).all()
    df["lookup_error"] = df.coverage_state.eq("lookup_error")
    output.mkdir(exist_ok=True, parents=True)
    tables = {}
    for label, cols in {
        "city": ["sampling_unit", "city", "region", "regional"],
        "region": ["region"],
        "route_family": ["route_family"],
        "road_type": ["osm_type"],
        "city_route_family": ["sampling_unit", "city", "route_family"],
    }.items():
        summary = (
            df.groupby(cols)
            .agg(
                candidates=("location_id", "size"),
                matched=("covered", "sum"),
                errors=("lookup_error", "sum"),
            )
            .reset_index()
        )
        summary["missing"] = summary.candidates - summary.matched - summary.errors
        summary["coverage_pct"] = (
            100 * summary.matched / (summary.candidates - summary.errors)
        )
        summary["candidate_share_pct"] = 100 * summary.candidates / len(df)
        summary["matched_share_pct"] = 100 * summary.matched / df.covered.sum()
        summary.to_csv(output / f"coverage_by_{label}.csv", index=False)
        tables[label] = summary
    city = tables["city"]
    probability = city[city.regional.eq(1)].groupby("region").coverage_pct
    region = probability.agg(["size", "mean", "sem"]).reset_index().sort_values("mean")
    half = t.ppf(0.975, region["size"] - 1) * region["sem"]
    region["lower"], region["upper"] = (
        (region["mean"] - half).clip(lower=0),
        (region["mean"] + half).clip(upper=100),
    )
    region.to_csv(output / "regional_panel.csv", index=False)
    families = tables["city_route_family"]
    street = (
        families[families.route_family.eq("street")]
        .set_index("sampling_unit")
        .coverage_pct
    )
    families["street_pct_same_city"] = families.sampling_unit.map(street)
    families["difference_pp"] = families.coverage_pct - families.street_pct_same_city
    diff = (
        families.dropna(subset=["difference_pp"])
        .groupby("route_family")
        .difference_pp.agg(["mean", "size"])
    )
    diff.to_csv(output / "within_city.csv")
    plt.switch_backend("Agg")
    fig, axes = plt.subplots(
        2, 3, figsize=(8.1, 3.9), sharex=True, sharey=True, layout="constrained"
    )
    bins = np.arange(0, 101, 10)
    for ax, row in zip(
        axes.flat, region.sort_values("mean", ascending=False).itertuples(), strict=True
    ):
        xs = city.loc[city.region.eq(row.region) & city.regional.eq(1), "coverage_pct"]
        ax.hist(xs, bins=bins, color="#496579", edgecolor="white", linewidth=0.8)
        label = REGION_NAMES.get(row.region, row.region)
        ax.set_title(label, fontsize=10)
        ax.set_xlim(0, 100)
        ax.set_xticks([0, 50, 100])
        ax.set_yticks([0, 5, 10])
        ax.tick_params(labelsize=9)
        ax.grid(axis="y", color=".91", linewidth=0.6)
        ax.set_axisbelow(True)
        ax.spines[["right", "top"]].set_visible(False)
    fig.supxlabel("Sampled locations with a nearby panorama (%)", fontsize=11)
    fig.supylabel("Number of cities", fontsize=11)
    fig.savefig(Path("figs") / "regional_coverage.pdf")
    fig.savefig(Path("figs") / "regional_coverage.png", dpi=160)
    plt.close(fig)

    region_table = region[["region", "size", "mean"]].copy()
    region_table["region"] = region_table.region.replace(REGION_NAMES)
    region_table["interval"] = [
        f"[{lo:.1f}, {hi:.1f}]" for lo, hi in zip(region.lower, region.upper)
    ]
    region_table.to_latex(
        output / "regions.tex",
        index=False,
        escape=True,
        column_format="lrrr",
        header=["Region", "Cities", "Mean (\\%)", "95\\% interval"],
        float_format="%.1f",
    )
    routes = tables["route_family"].join(diff, on="route_family", validate="one_to_one")
    routes["route_family"] = routes.route_family.map(FAMILIES)
    routes[["route_family", "candidates", "coverage_pct", "size", "mean"]].to_latex(
        output / "routes.tex",
        index=False,
        escape=True,
        column_format="lrrrr",
        header=[
            "Route family",
            "Points",
            "Matched (\\%)",
            "Cities paired",
            "Difference (pp)",
        ],
        formatters={
            "candidates": "{:,}".format,
            "coverage_pct": "{:.1f}".format,
            "mean": "{:+.1f}".format,
        },
    )
    df["google_status"] = np.select(
        [df.covered, df.lookup_error],
        ["matched", "lookup_error"],
        default="no_panorama",
    )
    counts = df.groupby(
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

    by_city = df.groupby("sampling_unit").covered.sum()
    pilot = df[df.rank_in_city.le(100)]
    issued = df.coverage_state.eq("selected")
    values = {
        "RegionHigh": f"{region['mean'].max():.1f}",
        "RegionLow": f"{region['mean'].min():.1f}",
        "WalkGap": f"{diff.at['walking_route', 'mean']:+.1f}",
        "ServiceGap": f"{diff.at['service_road', 'mean']:+.1f}",
        "Sites": len(city),
        "Points": len(df),
        "Matches": df.covered.sum(),
        "MatchPct": f"{100 * df.covered.sum() / (~df.lookup_error).sum():.1f}",
        "Misses": (~df.covered & ~df.lookup_error).sum(),
        "Errors": df.lookup_error.sum(),
        "Selected": issued.sum(),
        "UniquePanos": df.loc[issued, "panorama_key"].nunique(),
        "NoHit": by_city.eq(0).sum(),
        "Full": by_city.ge(100).sum(),
        "Partial": by_city.between(1, 99).sum(),
        "PilotPct": f"{100 * pilot.covered.mean():.1f}",
    }
    lines = [
        r"\newcommand{"
        + chr(92)
        + name
        + "}{"
        + (f"{v:,}" if isinstance(v, (int, np.integer)) else v)
        + "}"
        for name, v in values.items()
    ]
    (output / "numbers.tex").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
