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

# How many jobs to build with. nproc on Linux, sysctl on a Mac, and 1 if this
# machine has neither, because an empty -j means unlimited rather than default.
JOBS ?= $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
FIRMWARE = build/pico_train_display_$(BOARD)_$(VERSION).uf2

# Which machine this is. Only the dependency targets care: the firmware and the
# simulator build the same way either way, but Debian and a Mac name the
# toolchain differently and install it with different tools.
HOST ?= $(if $(filter Darwin,$(shell uname -s)),macos,linux)

.PHONY: sim test firmware unix-port command-line-tools \
	firmware-depend firmware-depend-linux firmware-depend-macos \
	sim-depend sim-depend-linux sim-depend-macos

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
firmware: | $(MICROPYTHON_DIR)
	$(MAKE) -C $(MICROPYTHON_DIR)/mpy-cross
	$(MAKE) -C $(MICROPYTHON_DIR)/ports/rp2 BOARD=$(BOARD) submodules
	$(MAKE) -C $(MICROPYTHON_DIR)/ports/rp2 -j $(JOBS) BOARD=$(BOARD) \
		FROZEN_MANIFEST=$(CURDIR)/manifest.py
	@python3 tools/check_firmware.py $(RP2_BUILD)/firmware.uf2 \
		--board $(BOARD) --elf $(RP2_BUILD)/firmware.elf
	@mkdir -p $(dir $(FIRMWARE))
	@cp $(RP2_BUILD)/firmware.uf2 $(FIRMWARE)
	@echo 'Flash this: $(FIRMWARE)'

# The cross toolchain the firmware needs, which is not what the simulator
# needs: one builds for the board, the other for this machine. This installs
# whichever of the two below suits the machine it is run on; name one directly
# to install the other, say when building in a Linux container on a Mac.
firmware-depend: firmware-depend-$(HOST) | $(MICROPYTHON_DIR)

firmware-depend-linux:
	sudo apt-get update
	sudo apt-get install -y cmake gcc-arm-none-eabi \
		libnewlib-arm-none-eabi build-essential

# Homebrew's arm-none-eabi-gcc formula is the compiler with no libc behind it,
# so every #include <stdio.h> in the firmware fails to resolve. The cask is
# ARM's own build, which brings newlib with it. That build also loads
# Homebrew's libzstd from a fixed path, so cc1 will not start without zstd
# present, however little the firmware has to do with compressing anything.
#
# Installing the cask runs a pkg, so it asks for a password, in the way the
# apt-get above does.
firmware-depend-macos: command-line-tools
	brew install cmake zstd
	brew install --cask gcc-arm-embedded

# Everything the simulator needs, from nothing. Host tools only, so the split
# here is just the packages: what to build once they are installed is the same
# on either machine.
sim-depend: sim-depend-$(HOST) | $(MICROPYTHON_DIR)
	$(MAKE) -C $(MICROPYTHON_DIR)/ports/unix submodules
	$(MAKE) unix-port

sim-depend-linux:
	sudo apt-get update
	sudo apt-get install -y build-essential git pkg-config

sim-depend-macos: command-line-tools
	brew install pkg-config

# The clang, make and git that both builds compile host tools with. A Mac
# installs them through a dialog rather than a package manager, so there is
# nothing to run unattended here and this only says what is missing.
command-line-tools:
	@xcode-select -p >/dev/null 2>&1 || { \
		echo 'Xcode command line tools are missing: xcode-select --install'; \
		exit 1; }

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
