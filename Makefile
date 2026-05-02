# Zen Club — dependency install and common tasks (no pip package layout).

PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help install run schema clean lint

help:
	@echo "Targets:"
	@echo "  make install   Create $(VENV) and install requirements.txt"
	@echo "  make run       Start Zen Club with data/code_group.json"
	@echo "  make schema    Print the profile JSON Schema"
	@echo "  make clean     Remove venv and Python caches"
	@echo "  make lint      Syntax-check zen_club.py and zen_club_core.py"

install: $(VENV)/bin/python

$(VENV)/bin/python: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r requirements.txt

run: install
	$(PY) zen_club.py --profile data/code_group.json

schema: install
	$(PY) zen_club.py --schema

clean:
	rm -rf $(VENV)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true

lint: install
	$(PY) -m py_compile zen_club.py zen_club_core.py zen_analytics.py zen_search.py zen_transcript.py zen_interview.py
