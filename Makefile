PY ?= python3

.PHONY: exhibits paper
exhibits:
	mkdir -p tabs figs
	$(PY) scripts/coverage.py
	$(PY) scripts/sightings.py

paper: exhibits
	cd paper && latexmk -pdf -interaction=nonstopmode -halt-on-error street_presence.tex
