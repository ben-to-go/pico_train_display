import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401

# Stub framebuf and display base classes for desktop CPython unit testing
framebuf_mod = sys.modules.setdefault('framebuf', types.ModuleType('framebuf'))
if not hasattr(framebuf_mod, 'FrameBuffer'):
  class DummyFrameBuffer:
    def __init__(self, buffer, width, height, format):
      pass
    def fill(self, col):
      pass
  framebuf_mod.FrameBuffer = DummyFrameBuffer
if not hasattr(framebuf_mod, 'GS4_HMSB'):
  framebuf_mod.GS4_HMSB = 2

display_mod = sys.modules.setdefault('display', types.ModuleType('display'))
class DummyDisplay:
  def __init__(self, *args, **kwargs):
    pass
  def fill(self, col):
    pass
display_mod.Display = DummyDisplay

from ssd1322 import SSD1322, _find_changed_rows


class MockBus:

  def __init__(self):
    self.writes = []
    self.commands = []
    self.reset_called = False

  def reset(self):
    self.reset_called = True

  def write(self, buf, dc):
    data = bytes(buf)
    self.writes.append((data, dc))
    if dc == 0:
      self.commands.append(data[0])


class SSD1322PartialFlushTest(unittest.TestCase):

  def test_find_changed_rows(self):
    row_bytes = 128
    rows = 64
    b1 = bytearray(row_bytes * rows)
    b2 = bytearray(row_bytes * rows)

    # Identical buffers
    self.assertIsNone(_find_changed_rows(b1, b2, row_bytes, rows))

    # Single row changed
    b1[20 * row_bytes + 10] = 0xFF
    self.assertEqual((20, 20), _find_changed_rows(b1, b2, row_bytes, rows))

    # Multiple rows changed (span 20..28)
    b1[28 * row_bytes + 5] = 0xAA
    self.assertEqual((20, 28), _find_changed_rows(b1, b2, row_bytes, rows))

  def test_initial_flush_writes_full_frame(self):
    bus = MockBus()
    display = SSD1322(bus, width=256, height=64)
    bus.writes.clear()
    bus.commands.clear()

    # Force all dirty and flush
    display._all_dirty = True
    display.flush()

    # Command 0x75 sets row range (0, 63)
    row_cmds = [w for w in bus.writes if len(w[0]) == 2 and w[1] == 1]
    self.assertIn((bytes([0, 63]), 1), row_cmds)
    # Full data write (8192 bytes)
    data_writes = [w for w in bus.writes if len(w[0]) == 8192 and w[1] == 1]
    self.assertEqual(1, len(data_writes))

  def test_subsequent_flush_with_no_changes_is_noop(self):
    bus = MockBus()
    display = SSD1322(bus, width=256, height=64)
    bus.writes.clear()

    display.flush()
    # Zero bus writes issued because nothing changed in the buffer
    self.assertEqual(0, len(bus.writes))

  def test_partial_flush_sends_only_modified_row_slice(self):
    bus = MockBus()
    display = SSD1322(bus, width=256, height=64)
    bus.writes.clear()

    # Modify rows 20..28 (e.g. calling-at scrolling text line)
    row_bytes = 128
    for r in range(20, 29):
      display._buffer[r * row_bytes + 10] = 0xFF

    display.flush()

    # Row range command set to 20..28
    row_cmds = [w for w in bus.writes if len(w[0]) == 2 and w[1] == 1]
    self.assertIn((bytes([20, 28]), 1), row_cmds)

    # Data write sends exactly 9 rows * 128 bytes = 1152 bytes (not 8192)
    expected_bytes = 9 * 128
    data_writes = [w for w in bus.writes if len(w[0]) == expected_bytes and w[1] == 1]
    self.assertEqual(1, len(data_writes))

    # Next flush with no changes does nothing
    bus.writes.clear()
    display.flush()
    self.assertEqual(0, len(bus.writes))


if __name__ == '__main__':
  unittest.main()
