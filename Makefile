# Makefile
VENV := .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

.PHONY: setup run clean

# Idempotent setup: only updates if requirements.txt changed or venv is missing
$(VENV)/bin/activate: requirements.txt
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@touch $(VENV)/bin/activate

setup: $(VENV)/bin/activate

# Example run command using the config file
run: setup
	$(PYTHON) git_sync.py --config config.yaml --path ./workdir --debug

clean:
	rm -rf $(VENV)
	rm -f git_sync_*.log

