"""Generate manuscript counts from the frozen Google image and review ledgers."""

from pathlib import Path

import pandas as pd

CATEGORIES = [
    "women_count",
    "men_count",
    "women_twowheeler",
    "men_twowheeler",
    "unclear_count",
]


def percent(numerator, denominator):
    return f"{100 * numerator / denominator:.1f}" if denominator else "---"


def main():
    cities = pd.read_csv("data/cities.csv")
    images = pd.read_csv("data/images.csv.gz")
    assert images.location_id.is_unique and images.n_person.notna().all()
    positive = images.n_person.ge(1)
    assert images.loc[positive, CATEGORIES].notna().all().all()
    assert images.loc[~positive, CATEGORIES].isna().all().all()
    observed = images.loc[positive, CATEGORIES]
    assert (observed.ge(0) & observed.eq(observed.round())).all().all()
    images["occupied"] = images[CATEGORIES].sum(axis=1, min_count=1).gt(0)
    grouped = images.groupby(["sampling_unit", "city"])
    counts = grouped.agg(
        images_screened=("location_id", "size"),
        images_sent_to_muse=("women_count", "count"),
        muse_images_with_people=("occupied", "sum"),
    ).join(grouped[CATEGORIES].sum(min_count=1).astype("Int64"))
    counts.to_csv("tabs/muse_by_city.csv")
    sites = cities.merge(
        counts.reset_index(),
        how="left",
        on=["sampling_unit", "city"],
        validate="one_to_one",
    )
    assert sites.images_screened.sum() == len(images)
    w, m, wr, mr, unclear = images[CATEGORIES].sum().astype(int)
    total = w + m + wr + mr + unclear
    years = images.capture_year.astype("Int64").astype("string")
    values = {
        "PreferredYear": int(years.eq("2026").sum()),
        "EarlyDates": int(years.eq("1970").sum()),
        "Images": len(images),
        "ImageSites": (sites.images_screened > 0).sum(),
        "InitialImages": images.batch.eq("round1").sum(),
        "ReserveImages": images.batch.eq("november_reserve").sum(),
        "Sent": positive.sum(),
        "Unsent": (~positive).sum(),
        "Occupied": int(sites.muse_images_with_people.sum()),
        "ModelEmpty": int(positive.sum() - sites.muse_images_with_people.sum()),
        "HumanReviews": sites.human_submitted.sum(),
        "ClassPed": w + m,
        "ClassRider": wr + mr,
        "Sightings": total,
        "Unclear": unclear,
        "PedPct": percent(w, w + m),
        "RiderPct": percent(wr, wr + mr),
        "UnclearPct": percent(unclear, total),
    }
    lines = []
    for name, value in values.items():
        display = value if isinstance(value, str) else f"{value:,}"
        lines.append(r"\newcommand{\MW" + name + "}{" + display + "}")
    dest = Path("tabs")
    activity = pd.DataFrame(
        {
            "Activity": ["Walking or standing", "Riding a two-wheeler"],
            "Women": [w, wr],
            "Men": [m, mr],
        }
    )
    activity["Classified"] = activity.Women + activity.Men
    activity["Women (%)"] = [
        percent(a, b) for a, b in zip(activity.Women, activity.Classified)
    ]
    activity.to_latex(
        dest / "activity.tex",
        index=False,
        escape=True,
        column_format="lrrrr",
        formatters={k: "{:,}".format for k in ["Women", "Men", "Classified"]},
    )
    regions = []
    for region, group in sites.loc[sites.regional.eq(1)].groupby("region"):
        women, men = group[["women_count", "men_count"]].sum().astype(int)
        regions.append(
            {
                "region": region,
                "sampled": len(group),
                "with_images": int(group.images_screened.gt(0).sum()),
                "with_classified_pedestrians": int(
                    (group.women_count + group.men_count).gt(0).sum()
                ),
                "women": women,
                "men": men,
                "women_pct": percent(women, women + men),
            }
        )
    regional = pd.DataFrame(regions)
    regional.to_csv(dest / "regional_pedestrians.csv", index=False)
    regional["region"] = regional.region.replace(
        {
            "Middle East, North Africa, Afghanistan & Pakistan": "MENA, Afghanistan & Pakistan"
        }
    )
    regional.to_latex(
        dest / "regional_pedestrians.tex",
        index=False,
        escape=True,
        column_format="lrrrrrr",
        header=[
            "Region",
            "Drawn",
            "Imaged",
            "Classified",
            "Women",
            "Men",
            "Women (\\%)",
        ],
    )
    (dest / "sightings.tex").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
