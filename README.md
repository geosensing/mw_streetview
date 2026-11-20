# Measuring Women’s and Men’s Presence on Urban Streets: Evidence from Google Street View in 98 Cities

Women accounted for 28.8% of the pedestrians and 5.4% of the bicycle, motorcycle, and scooter riders that a vision model could classify as women or men in our Street View images. The model left 66% of all sightings unclear. The shares describe people the model could classify in the photographs we obtained. [Read the paper](paper/street_presence.pdf).

## Which Streets Did Google Photograph?

We started with a map. Within each of 98 cities, we drew 1,000 locations at random along mapped streets and walking routes. A kilometer of eligible footpath had the same chance of being sampled as a kilometer of residential street. We drew 75 cities in regional lotteries, drew 20 sites from our earlier photo study, and included five bike-study sites. Some of those selections covered the same urban area, giving us 98 distinct sites.

Google returned a nearby panorama for 53,595 of the 98,000 locations. In 16 cities it returned none. Even within the same city, a walking route was, on average, 12.6 percentage points less likely to have a nearby panorama than an ordinary street. A camera on a boulevard can also fall within range of a search on a parallel footpath. A successful search tells us where a camera was, but the camera may have faced away from the sampled route. [Coverage by city](tabs/coverage_by_city.csv) · [Within-city comparisons by route type](tabs/within_city.csv).

## Who Appeared in the Photographs?

We obtained 9,997 images across 82 cities. A person detector flagged 2,594 images for review. We sent each of those images to Muse, which counted pedestrians and riders separately. It counted 500 women and 1,237 men walking or standing, and 30 women and 528 men riding two-wheelers. It also counted 4,449 people it could not classify. Its response combined unclear pedestrians and riders in a single category. Each observation is a sighting: a person photographed twice can contribute twice. [City-level counts](tabs/muse_by_city.csv) · [Counting instructions](paper/protocol/prompt.txt).

The detector skipped 7,403 images. Estimating how often it missed a person requires human review of a sample of those images. A separate question is who used streets where Google had no imagery. For example, all 13 South Asian cities in the regional lottery contributed classified pedestrians, compared with six of 13 cities in sub-Saharan Africa. The regional file gives both the number of cities drawn and the number contributing sightings. [Regional pedestrian counts](tabs/regional_pedestrians.csv).

## Related Research

The companion study starts with photographs that people contributed to Mapillary and KartaView in 101 cities. It uses the same counting categories. Starting with street locations here lets us measure how often a sampled street fails to yield a photograph. [101-city crowdsourced study](https://github.com/geosensing/missing_women_crowdsourced_photos_multicountry) · [Earlier Street View study in India](https://github.com/geosensing/streetsight).

## Reproduce the paper

The [city roster](data/cities.csv) records which sites came from each draw. The [location file](data/locations.csv.gz) has one row per sampled road position, including unsuccessful lookups. The [image file](data/images.csv.gz) has one row per acquired image. Join them on `location_id`. A blank Muse count means that the detector found no person and the image did not go to Muse. A zero means that Muse examined the image and counted nobody in that category.

With Python 3.12 or later, Make, and a TeX installation that includes `latexmk` and BibTeX, run these commands from the repository root.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
make paper PY=.venv/bin/python
```

[Coverage analysis](scripts/coverage.py) regenerates the comparisons by city and road type, the regional [figure](figs/regional_coverage.pdf), and the comparison to the Global Streetscapes catalog. [Sightings analysis](scripts/sightings.py) regenerates the detector and Muse totals and the tables in the paper. Both use the frozen records and run offline. Use `make exhibits` for the [tables](tabs/) and figure alone.

[Collection scripts](scripts/collection/) document the original city draw, road sampling, panorama searches, image acquisition, person detection, and Muse submissions. These stages used external services, licensed imagery, and local field-study records. [Data and collection notes](data/README.md) describe the inputs and the identifiers used in the public files.
