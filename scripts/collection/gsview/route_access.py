"""Pedestrian eligibility and sidewalk representation for the expanded study frame."""

import numpy as np
import pandas as pd
import pyarrow as pa
import shapely
from pyproj import Transformer

POSITIVE = {"yes", "designated", "permissive", "official"}
LIMITED = {"motorway", "motorway_link", "trunk_link"}
WALKING = {"footway", "path", "steps"}


def sidewalk_parents(geometry, streets, walks, area):
    """Match tagged sidewalks to the same nearby street at three interior positions."""
    matches = {}
    if not len(streets) or not len(walks):
        return matches
    lon, lat = area.representative_point().coords[0]
    project = Transformer.from_crs(
        4326, f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84", always_xy=True
    ).transform

    def metric(lines):
        return shapely.transform(lines, project, interleaved=False)

    street_lines = metric(geometry[streets])
    tree = shapely.STRtree(street_lines)
    lines = metric(geometry[walks])
    answers = []
    for fraction in (0.25, 0.5, 0.75):
        pts = shapely.line_interpolate_point(lines, fraction, normalized=True)
        pairs = tree.query_nearest(pts, max_distance=20, all_matches=False)
        nearest = np.full(len(walks), -1)
        nearest[pairs[0]] = streets[pairs[1]]
        answers.append(nearest)
    same = (answers[0] >= 0) & (answers[0] == answers[1]) & (answers[0] == answers[2])
    idx = np.flatnonzero(same)
    parents = answers[0][idx]
    parent_lines = street_lines[np.searchsorted(streets, parents)]
    first = shapely.line_interpolate_point(lines[idx], 0.25, normalized=True)
    last = shapely.line_interpolate_point(lines[idx], 0.75, normalized=True)
    advance = np.abs(
        shapely.line_locate_point(parent_lines, last)
        - shapely.line_locate_point(parent_lines, first)
    )
    parallel = advance >= 0.7 * shapely.distance(first, last)
    return dict(zip(walks[idx[parallel]], parents[parallel], strict=True))


def prepare_routes(roads, area):
    df = roads.to_pandas()
    geom = shapely.from_wkb(df.geometry.to_numpy())
    shapely.prepare(area)
    valid = np.isin(shapely.get_type_id(geom), [1, 5]) & shapely.intersects(area, geom)
    df = df.loc[valid].reset_index(drop=True)
    geom = geom[valid]
    tags = (
        df[["highway", "access", "foot", "sidewalk", "service", "footway"]]
        .fillna("")
        .apply(lambda values: values.str.strip().str.lower())
    )
    effective = tags.foot.where(tags.foot.ne(""), tags.access)
    positive = effective.isin(POSITIVE)
    ordinary = positive | effective.isin(["", "unknown", "discouraged"])
    limited = tags.highway.isin(LIMITED)
    service = tags.highway.eq("service")
    pedestrian = tags.highway.isin(WALKING)
    tagged_walk = pedestrian & tags.footway.eq("sidewalk")
    crossing = pedestrian & tags.footway.eq("crossing")
    sidewalk_evidence = tags.sidewalk.isin(["yes", "both", "left", "right", "separate"])
    restricted_service = tags.service.isin(
        [
            "driveway",
            "parking_aisle",
            "drive-through",
            "drive_through",
            "emergency_access",
        ]
    )
    allowed_service = ~restricted_service & (
        tags.service.isin(["", "alley"]) | positive
    )
    keep = ordinary & (~service | allowed_service) & ~tagged_walk & ~crossing
    # Generic permission to use a motorway does not grant permission to walk on it.
    keep &= ~limited | tags.foot.isin(POSITIVE) | sidewalk_evidence
    proxy = keep & limited & ~tags.foot.isin(POSITIVE) & sidewalk_evidence
    family = np.select(
        [
            pedestrian,
            service,
            tags.highway.isin(["pedestrian", "living_street"]),
            tags.highway.str.endswith("_link") | tags.highway.eq("motorway"),
        ],
        [
            "walking_route",
            "service_road",
            "pedestrian_or_shared_street",
            "connector_or_motorway",
        ],
        default="street",
    )
    df["route_family"] = family
    df["access_status"] = np.where(
        effective.isin(["", "unknown"]), "unverified", effective
    )
    df["eligibility_reason"] = np.where(
        positive, "pedestrian_access_allowed", "ordinary_route_access_unverified"
    )
    df.loc[effective.eq("discouraged"), "eligibility_reason"] = "walking_discouraged"
    df.loc[proxy, "eligibility_reason"] = "sidewalk_centerline_proxy"
    matches = sidewalk_parents(
        geom, np.flatnonzero(~pedestrian), np.flatnonzero(tagged_walk & ordinary), area
    )
    exceptions = []
    for walk, parent in matches.items():
        sidepath = effective.iat[parent] in {"use_sidepath", "no"}
        limited_parent = (
            limited.iat[parent]
            and ordinary.iat[parent]
            and tags.foot.iat[parent] not in POSITIVE
        )
        if not (sidepath or limited_parent):
            continue
        keep.iat[walk] = True
        keep.iat[parent] = False
        df.at[walk, "eligibility_reason"] = "mapped_sidewalk_for_restricted_carriageway"
        exceptions.append((df.id.iat[walk], df.id.iat[parent], "sidewalk"))
    for parent in np.flatnonzero(proxy & keep):
        exceptions.append((None, df.id.iat[parent], "sidewalk_centerline_proxy"))
    decisions = (
        pd.DataFrame(
            {
                "foot": tags.foot,
                "access": tags.access,
                "effective_access": effective,
                "included": keep,
            }
        )
        .value_counts()
        .reset_index(name="ways")
    )
    return (
        pa.Table.from_pandas(df[keep], preserve_index=False),
        pd.DataFrame(
            exceptions, columns=["sidewalk_id", "street_id", "representation"]
        ),
        decisions,
    )
