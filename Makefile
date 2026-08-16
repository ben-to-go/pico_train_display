# Running this project on a desktop. The firmware itself is built by
# .github/workflows/build.yml, which is the canonical build.
#
# MICROPYTHON_DIR points at a MicroPython checkout; ~/micropython by default.

MICROPYTHON_DIR ?= $(HOME)/micropython
export MICROPYTHON ?= $(MICROPYTHON_DIR)/ports/unix/build-standard/micropython

.PHONY: sim test unix-port

# Runs main.py against config.json, with the panel drawn in the terminal.
sim:
	@sim/run.sh

test:
	@python3 -m unittest discover -s tests

# src/ is MicroPython, not Python, so the simulator needs a MicroPython that
# runs on a desktop. MICROPY_PY_FFI=0 avoids needing libffi-dev, and the GIL
# matters because main.run() renders on a second thread while the unix port
# builds without one. Cleaning first because changing either flag leaves the
# generated qstrs stale, which fails as MP_QSTR_GIL undeclared.
unix-port:
	@$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix clean
	@$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix \
		MICROPY_PY_FFI=0 MICROPY_PY_THREAD_GIL=1
