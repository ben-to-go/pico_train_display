# Running this project on a desktop. The firmware itself is built by
# .github/workflows/build.yml, which is the canonical build.
#
# MICROPYTHON_DIR points at a MicroPython checkout; ~/micropython by default.

MICROPYTHON_DIR ?= $(HOME)/micropython
# The version the workflow builds against, so the simulator runs what the
# firmware is made of.
MICROPYTHON_VERSION ?= v1.28.0
export MICROPYTHON ?= $(MICROPYTHON_DIR)/ports/unix/build-standard/micropython

ACT ?= $(if $(wildcard bin/act),./bin/act,act)
ACT_ARTIFACTS ?= artifacts
# act's smallest image has no apt or compiler, and the workflow needs both.
ACT_IMAGE ?= catthehacker/ubuntu:act-latest

.PHONY: sim test depend unix-port act

# Runs main.py against config.json, with the panel drawn in the terminal.
sim:
	@sim/run.sh

test:
	@python3 -m unittest discover -s tests

# Everything the simulator needs, from nothing.
depend:
	sudo apt-get update
	sudo apt-get install -y build-essential git pkg-config
	@test -d $(MICROPYTHON_DIR) || git clone --depth 1 \
		--branch $(MICROPYTHON_VERSION) \
		https://github.com/micropython/micropython $(MICROPYTHON_DIR)
	$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix submodules
	$(MAKE) unix-port

# src/ is MicroPython, not Python, so the simulator needs a MicroPython that
# runs on a desktop. MICROPY_PY_FFI=0 avoids needing libffi-dev, and the GIL
# matters because main.run() renders on a second thread while the unix port
# builds without one. Cleaning first because changing either flag leaves the
# generated qstrs stale, which fails as MP_QSTR_GIL undeclared.
unix-port:
	@$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix clean
	@$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix \
		MICROPY_PY_FFI=0 MICROPY_PY_THREAD_GIL=1

# Runs .github/workflows/build.yml in containers, the way the runner does.
# Needs Docker reachable as you: sudo usermod -aG docker $$USER, then log in
# again. The firmware lands in $(ACT_ARTIFACTS), as it does on a real run.
act:
	@mkdir -p $(ACT_ARTIFACTS)
	$(ACT) --artifact-server-path $(ACT_ARTIFACTS) -P ubuntu-latest=$(ACT_IMAGE)
