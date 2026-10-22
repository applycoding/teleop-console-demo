PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
.PHONY: run test docker-build docker-up
run:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m teleop.server
test:
	$(PYTHON) -m pytest -q
docker-build:
	docker compose build
docker-up:
	docker compose up --build
