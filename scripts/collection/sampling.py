"""Allocate and independently draw the regional and prior-study panels."""

import hashlib
from collections import defaultdict
from math import comb


def quotas(sizes, budget):
    """Equal regional quotas; award ties to larger frames, then by name."""
    if budget < len(sizes) or budget > sum(sizes.values()):
        raise ValueError("Regional budget must cover each region and fit the frame")
    allocation = {region: 0 for region in sizes}
    order = sorted(sizes, key=lambda region: (-sizes[region], region))
    remaining = budget
    while remaining:
        for region in order:
            if remaining and allocation[region] < sizes[region]:
                allocation[region] += 1
                remaining -= 1
    return allocation


def sample(ids, n, seed, panel):
    """Seeded hash ordering prevents source row order from changing the draw."""
    if n > len(ids) or len(set(ids)) != len(ids):
        raise ValueError("Invalid sample frame or target")

    def priority(identifier):
        return hashlib.sha256(f"{seed}|{panel}|{identifier}".encode()).digest()

    return set(sorted(ids, key=lambda key: (priority(key), key))[:n])


def design(frame, prior_matches, anchors, regional_n, prior_n, seed=None):
    regions = defaultdict(list)
    for city in frame:
        regions[city["region"]].append(city["id"])
    allocation = quotas({k: len(v) for k, v in regions.items()}, regional_n)
    # Draw original corpus IDs, including sites outside the GHSL polygons.
    eligible = sorted(prior_matches)
    if not 0 < prior_n <= len(eligible):
        raise ValueError("Invalid prior-study budget")
    prior_sites = defaultdict(list)
    for site, center in prior_matches.items():
        if center:
            prior_sites[center].append(site)
    prior_draw = sample(eligible, prior_n, seed, "prior") if seed is not None else set()
    regional_draw = set()
    if seed is not None:
        for region, ids in regions.items():
            regional_draw.update(
                sample(ids, allocation[region], seed, "world:" + region)
            )
    result = []
    for city in frame:
        key, region = city["id"], city["region"]
        world = allocation[region] / len(regions[region])
        site_ids = prior_sites.get(key, [])
        prior = 1 - comb(len(eligible) - len(site_ids), prior_n) / comb(
            len(eligible), prior_n
        )
        anchor = key in anchors
        status = anchors.get(key, "")
        row = dict(
            city,
            prior_sites="|".join(sorted(site_ids)),
            bike_sites=status,
            p_regional=world,
            p_prior=prior,
            p_any=1.0 if anchor else world + prior - world * prior,
        )
        if seed is not None:
            row.update(
                regional=int(key in regional_draw),
                prior=int(bool(prior_draw.intersection(site_ids))),
                anchor=int(anchor),
                selected=int(
                    anchor
                    or key in regional_draw
                    or bool(prior_draw.intersection(site_ids))
                ),
            )
        result.append(row)
    review = []
    for region, ids in sorted(regions.items()):
        matched_sites = sum(len(prior_sites.get(key, [])) for key in ids)
        review.append(
            dict(
                region=region,
                urban_centers=len(ids),
                matched_prior=matched_sites,
                completed_anchor_centers=sum(
                    "completed" in anchors.get(key, "") for key in ids
                ),
                planned_anchor_centers=sum(
                    "planned" in anchors.get(key, "") for key in ids
                ),
                regional_draw=allocation[region],
            )
        )
    return result, review, prior_draw
