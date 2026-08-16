# Running this project on a desktop. The firmware itself is built by
# .github/workflows/build.yml, which is the canonical build.
#
# MICROPYTHON_DIR points at a MicroPython checkout; ~/micropython by default.

MICROPYTHON_DIR ?= $(HOME)/micropython
# Read out of the workflow, so the pin cannot drift from the firmware's.
MICROPYTHON_VERSION ?= $(shell sed -n 's|.*refs/tags/\(v[0-9.]*\).*|\1|p' \
	.github/workflows/build.yml)
export MICROPYTHON ?= $(MICROPYTHON_DIR)/ports/unix/build-standard/micropython

ACT ?= ./bin/act
ACT_VERSION ?= v0.2.89

.PHONY: sim test sim-depend act-depend unix-port act

# Runs main.py against config.json, with the panel drawn in the terminal.
sim:
	@sim/run.sh

test:
	@python3 -m unittest discover -s tests

# Everything the simulator needs, from nothing. Host tools only: the cross
# toolchain the firmware needs is build.yml's, and the two are meant to differ.
sim-depend:
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

# act itself, into bin/. Installing Docker needs decisions about your machine
# that a make target should not be making, so this only checks for it.
act-depend:
	@docker info >/dev/null 2>&1 || { \
		echo 'act runs the workflow in containers, so it needs Docker:'; \
		echo '  https://docs.docker.com/engine/install/'; \
		echo '  sudo usermod -aG docker $$USER   # then log in again'; \
		exit 1; }
	@mkdir -p bin
	curl -sSfL \
		https://raw.githubusercontent.com/nektos/act/$(ACT_VERSION)/install.sh \
		| bash -s -- -b bin $(ACT_VERSION)

# Runs .github/workflows/build.yml in containers; firmware lands in artifacts/.
# The flags are in .actrc, so running act by hand behaves the same. act ignores
# job-level permissions, so this says nothing about the tag-gated release step.
act:
	@$(ACT)
