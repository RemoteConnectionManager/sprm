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

config_merged.yaml: setup merge_blame.py config_general.yaml config_rcm_old.yaml
	$(PYTHON) merge_blame.py config_general.yaml config_rcm_old.yaml > config_merged.yaml

# Example run command using the config file
run: setup config_merged.yaml
	$(PYTHON) sprm.py --config config_merged.yaml 
run_cluster: setup config_merged.yaml
	$(PYTHON) sprm.py --config config_merged.yaml --path $TMPDIR/$USER/spack_rcm_packages

clean:
	rm -rf $(VENV)
	rm -f git_sync_*.log

