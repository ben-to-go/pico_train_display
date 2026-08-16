#!/usr/bin/env python3
# Copyright (c) 2023 Tom Ward
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""Checks a built firmware against the board it is meant for.

Nothing here needs a device. It reads the .uf2 the build produced, and the
.elf beside it, and answers three questions a unit test cannot:

  will it flash        the UF2 family has to match, or the bootloader ignores
                       the file and the board looks broken
  will it fit          the firmware must end before the filesystem does, or
                       flashing eats the saved config
  will it run          static RAM has to leave room for MicroPython's heap

  tools/check_firmware.py firmware.uf2 --board RPI_PICO_W --elf firmware.elf
"""

import argparse
import collections
import struct
import sys

# UF2 is a container of 512 byte blocks, each carrying 256 bytes of payload
# and the address to write it to. Block layout, magic numbers and flags:
# https://github.com/microsoft/uf2#file-format
_UF2_MAGIC = (0x0A324655, 0x9E5D5157)
_UF2_BLOCK = 512
# "block should be skipped when writing the device flash", used for metadata.
_NOT_MAIN_FLASH = 0x00001000

# Flash is memory mapped for execute-in-place at 0x10000000, and SRAM begins
# at 0x20000000, on both chips.
# RP2040 datasheet 2.2 (SRAM) and 2.6.3 (XIP):
# https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf
# RP2350 datasheet 4.2 and 4.4:
# https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf
_FLASH_BASE = 0x10000000
_SRAM_BASE = 0x20000000

# Properties of the hardware, not of the build.
#
# Family IDs are the registry the bootloader matches against, so a file for
# the wrong chip is ignored rather than bricking anything:
# https://github.com/microsoft/uf2/blob/master/utils/uf2families.json
#
# Flash is the chip Raspberry Pi fit to the board, and SRAM is the die:
# https://datasheets.raspberrypi.com/picow/pico-w-datasheet.pdf
# https://datasheets.raspberrypi.com/picow/pico-2-w-datasheet.pdf
_BOARDS = {
    'RPI_PICO_W': {
        'family': 0xE48BFF56,  # RP2040
        'flash': 2 * 1024 * 1024,
        'ram': 264 * 1024,
        'filesystem': 848 * 1024,
    },
    'RPI_PICO2_W': {
        'family': 0xE48BFF59,  # RP2350, Arm secure
        'flash': 4 * 1024 * 1024,
        'ram': 520 * 1024,
        'filesystem': 2560 * 1024,
    },
}

# How much flash MicroPython keeps for its filesystem is a property of its rp2
# port rather than of the board, so it is worth checking after a version bump.
# Read it off a built image with `picotool info -a firmware.uf2`, which prints
# it as "embedded drive"; --filesystem-bytes overrides these.
#
# The firmware is useless without these, and a manifest mistake is quiet.
# Looked for as "<name>.py\0", which is how the frozen names are stored, so a
# module that merely gets mentioned somewhere does not count as present.
# What gets frozen is manifest.py, via:
# https://docs.micropython.org/en/latest/reference/manifest.html
_EXPECTED_FROZEN = ('main', 'trains', 'widgets', 'fallback', 'ssd1322')

# MicroPython allocates its heap from whatever SRAM the linker did not claim:
# https://docs.micropython.org/en/latest/reference/constrained.html
# There is no correct figure, so this is a guard rail rather than a spec. Both
# boards currently leave several times this much; it is here to catch a static
# buffer that swallows the heap.
_MIN_FREE_RAM = 64 * 1024


class Failure(Exception):
  """A check that did not pass."""


def read_uf2(path):
  """Returns {family: (lowest address, highest address, block count)}."""
  with open(path, 'rb') as f:
    data = f.read()
  if len(data) % _UF2_BLOCK:
    raise Failure('{} is not a whole number of UF2 blocks'.format(path))

  extents = collections.defaultdict(lambda: [None, 0, 0])
  contents = []
  for offset in range(0, len(data), _UF2_BLOCK):
    block = data[offset : offset + 32]
    magic0, magic1, flags, address, payload, _, _, family = struct.unpack(
        '<8I', block
    )
    if (magic0, magic1) != _UF2_MAGIC:
      raise Failure('bad UF2 magic at block {}'.format(offset // _UF2_BLOCK))
    if flags & _NOT_MAIN_FLASH:
      continue
    extent = extents[family]
    extent[0] = address if extent[0] is None else min(extent[0], address)
    extent[1] = max(extent[1], address + payload)
    extent[2] += 1
    # Kept apart from the block headers, so a string can be searched for
    # without a header splitting it in two.
    contents.append(data[offset + 32 : offset + 32 + payload])
  return (
      {family: tuple(e) for family, e in extents.items()},
      b''.join(contents),
  )


def static_ram(path, sram_bytes):
  """Bytes of SRAM the linker has already spoken for."""
  with open(path, 'rb') as f:
    elf = f.read()
  if elf[:4] != b'\x7fELF':
    raise Failure('{} is not an ELF file'.format(path))

  # ELF32 header: e_shoff at 0x20, e_shentsize at 0x2E, e_shnum at 0x30.
  # Figure 1-3 and 1-8 of the ELF specification:
  # https://refspecs.linuxfoundation.org/elf/elf.pdf
  section_offset, = struct.unpack_from('<I', elf, 0x20)
  section_size, = struct.unpack_from('<H', elf, 0x2E)
  section_count, = struct.unpack_from('<H', elf, 0x30)

  total = 0
  for i in range(section_count):
    # Section header: sh_flags at 0x08, sh_addr at 0x0C, sh_size at 0x14.
    # Note sh_addr and sh_size are not adjacent; sh_offset sits between them.
    header = section_offset + i * section_size
    sh_flags, sh_addr = struct.unpack_from('<II', elf, header + 0x08)
    sh_size, = struct.unpack_from('<I', elf, header + 0x14)
    # SHF_ALLOC, meaning the section occupies memory at run time. Counting
    # every such section in SRAM catches the vector table, .data, .bss, the
    # heap placeholder and the scratch banks, which is more than
    # arm-none-eabi-size reports as data + bss.
    if sh_flags & 0x2 and _SRAM_BASE <= sh_addr < _SRAM_BASE + sram_bytes:
      total += sh_size
  return total


def check(uf2_path, board_name, filesystem_bytes, elf_path):
  board = _BOARDS[board_name]
  if filesystem_bytes is None:
    filesystem_bytes = board['filesystem']
  extents, contents = read_uf2(uf2_path)
  failures = []

  print('{} for {}'.format(uf2_path, board_name))

  # Will it flash?
  if board['family'] not in extents:
    failures.append(
        'no blocks for family 0x{:08x}; found {}'.format(
            board['family'],
            ', '.join('0x{:08x}'.format(f) for f in sorted(extents)),
        )
    )
  else:
    low, high, blocks = extents[board['family']]
    print(
        '  family      0x{:08x}, {} blocks, {} .. {}'.format(
            board['family'], blocks, hex(low), hex(high)
        )
    )

  # Will it fit?
  if board['family'] in extents:
    _, high, _ = extents[board['family']]
    size = high - _FLASH_BASE
    filesystem_start = board['flash'] - filesystem_bytes
    headroom = filesystem_start - size
    print(
        '  flash       {:,} bytes of {:,}, filesystem starts at {:,},'
        ' headroom {:,}'.format(size, board['flash'], filesystem_start, headroom)
    )
    if headroom < 0:
      failures.append(
          'firmware runs {:,} bytes into the filesystem'.format(-headroom)
      )

  # Will it run?
  if elf_path:
    used = static_ram(elf_path, board['ram'])
    free = board['ram'] - used
    print(
        '  ram         {:,} bytes static of {:,}, {:,} left for the'
        ' heap'.format(used, board['ram'], free)
    )
    if free < _MIN_FREE_RAM:
      failures.append(
          'only {:,} bytes left for the heap, want at least {:,}'.format(
              free, _MIN_FREE_RAM
          )
      )

  # Is the application actually in there?
  missing = [
      m for m in _EXPECTED_FROZEN if (m + '.py\0').encode() not in contents
  ]
  print(
      '  frozen      {} of {} expected modules found'.format(
          len(_EXPECTED_FROZEN) - len(missing), len(_EXPECTED_FROZEN)
      )
  )
  if missing:
    failures.append('frozen modules missing: {}'.format(', '.join(missing)))

  for failure in failures:
    print('  FAILED      {}'.format(failure))
  return not failures


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('uf2')
  parser.add_argument('--board', required=True, choices=sorted(_BOARDS))
  parser.add_argument(
      '--filesystem-bytes',
      type=int,
      help='flash kept for the filesystem; defaults to what this port reserves',
  )
  parser.add_argument('--elf', help='firmware.elf, for the RAM check')
  args = parser.parse_args()

  try:
    ok = check(args.uf2, args.board, args.filesystem_bytes, args.elf)
  except Failure as e:
    print('  FAILED      {}'.format(e))
    ok = False
  print('  {}'.format('OK' if ok else 'NOT OK'))
  return 0 if ok else 1


if __name__ == '__main__':
  sys.exit(main())
