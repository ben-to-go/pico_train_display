# Running this project on a desktop. The firmware itself is built by
# .github/workflows/build.yml, which is the canonical build.

MICROPYTHON_DIR ?= $(HOME)/micropython
export MICROPYTHON ?= $(MICROPYTHON_DIR)/ports/unix/build-standard/micropython

.PHONY: help sim sim-compact test unix-port

help:
	@echo 'make sim          run the display in this terminal'
	@echo 'make sim-compact  the same, in braille, for terminals under 256 wide'
	@echo 'make test         run the unit tests'
	@echo 'make unix-port    build the MicroPython that the simulator runs on'
	@echo
	@echo 'MicroPython checkout: $(MICROPYTHON_DIR)'
	@echo 'Override with: make sim MICROPYTHON_DIR=/somewhere/else'

# Runs main.py against config.json, with the panel drawn in the terminal.
sim:
	@sim/run.sh

sim-compact:
	@sim/run.sh --compact

test:
	@python3 -m unittest discover -s tests

# MICROPY_PY_FFI=0 avoids needing libffi-dev. The GIL matters because
# main.run() renders on a second thread and the unix port builds without one.
unix-port:
	@$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix \
		MICROPY_PY_FFI=0 MICROPY_PY_THREAD_GIL=1
