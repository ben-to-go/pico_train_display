# Building and running this project. The workflow calls these same targets, so
# a green build is one you can reproduce here, and rebuilding after a change to
# src/ takes seconds rather than a fresh toolchain every time.
#
# MICROPYTHON_DIR points at a MicroPython checkout; ~/micropython by default.

MICROPYTHON_DIR ?= $(HOME)/micropython
# Pinned so a given tag of this project always builds the same firmware.
# RP2350, and so the Pico 2 W, needs at least v1.26.
MICROPYTHON_VERSION ?= v1.28.0
export MICROPYTHON ?= $(MICROPYTHON_DIR)/ports/unix/build-standard/micropython

BOARD ?= RPI_PICO2_W
RP2_BUILD = $(MICROPYTHON_DIR)/ports/rp2/build-$(BOARD)

# In the name of every image built, so a uf2 on a desktop somewhere can still
# be traced back to what made it. A tag on a release, and the tag plus the
# commit anywhere else.
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo unknown)
FIRMWARE = build/pico_train_display_$(BOARD)_$(VERSION).uf2

.PHONY: sim test firmware baked firmware-depend sim-depend unix-port

# Runs main.py against config.json, with the panel drawn in the terminal.
sim:
	@sim/run.sh

test:
	@python3 -m unittest discover -s tests

# The firmware, for one board. Everything the workflow used to spell out, so
# there is one description of how this project is built rather than two.
#
# Incremental: with the checkout and the build directory already there, a
# change to src/ is a few seconds, which is the point of having it here.
firmware: baked | $(MICROPYTHON_DIR)
	$(MAKE) -C $(MICROPYTHON_DIR)/mpy-cross
	$(MAKE) -C $(MICROPYTHON_DIR)/ports/rp2 BOARD=$(BOARD) submodules
	$(MAKE) -C $(MICROPYTHON_DIR)/ports/rp2 -j $(shell nproc) BOARD=$(BOARD) \
		FROZEN_MANIFEST=$(CURDIR)/manifest.py
	@python3 tools/check_firmware.py $(RP2_BUILD)/firmware.uf2 \
		--board $(BOARD) --elf $(RP2_BUILD)/firmware.elf
	@mkdir -p $(dir $(FIRMWARE))
	@cp $(RP2_BUILD)/firmware.uf2 $(FIRMWARE)
	@echo 'Flash this: $(FIRMWARE)'

# The tokens this image carries, out of the .env sim/run.sh already reads and
# into a module the manifest freezes. Into build/ because it is generated, and
# because nothing there is on the path a test or the simulator imports from.
baked:
	@mkdir -p build
	@set -a; [ -f .env ] && . ./.env; set +a; \
		python3 tools/write_baked.py build/baked.py

# The cross toolchain the firmware needs, which is not what the simulator
# needs: one builds for the board, the other for this machine.
firmware-depend: | $(MICROPYTHON_DIR)
	sudo apt-get update
	sudo apt-get install -y cmake gcc-arm-none-eabi \
		libnewlib-arm-none-eabi build-essential

# Everything the simulator needs, from nothing. Host tools only.
sim-depend: | $(MICROPYTHON_DIR)
	sudo apt-get update
	sudo apt-get install -y build-essential git pkg-config
	$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix submodules
	$(MAKE) unix-port

# The checkout both the firmware and the simulator are built from. A real
# target, so it is cloned once and then left alone; that checkout and its build
# directories are the cache that makes rebuilding quick.
$(MICROPYTHON_DIR):
	git clone --depth 1 --branch $(MICROPYTHON_VERSION) \
		https://github.com/micropython/micropython $@

# src/ is MicroPython, not Python, so the simulator needs a MicroPython that
# runs on a desktop. MICROPY_PY_FFI=0 avoids needing libffi-dev, and the GIL
# matters because main.run() renders on a second thread while the unix port
# builds without one. Cleaning first because changing either flag leaves the
# generated qstrs stale, which fails as MP_QSTR_GIL undeclared.
unix-port:
	@$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix clean
	@$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix \
		MICROPY_PY_FFI=0 MICROPY_PY_THREAD_GIL=1
