import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401

# Provide mock micropython module with const and viper decorators
micropython_mod = sys.modules.setdefault(
    'micropython', types.ModuleType('micropython')
)
if not hasattr(micropython_mod, 'const'):
  micropython_mod.const = lambda x: x
if not hasattr(micropython_mod, 'viper'):
  micropython_mod.viper = lambda fn: fn

# Mock machine module with Pin, mem32, and freq
machine_mod = sys.modules.setdefault('machine', types.ModuleType('machine'))


class MockPin:
  OUT = 1

  def __init__(self, pin, mode=None, value=None):
    self.pin = pin
    self.mode = mode
    self.val = value

  def __call__(self, val=None):
    if val is not None:
      self.val = val
    return self.val


class MockMem32:

  def __init__(self):
    self.gpio_out = 0

  def __getitem__(self, addr):
    return self.gpio_out

  def __setitem__(self, addr, val):
    self.gpio_out = val


machine_mod.Pin = MockPin
machine_mod.mem32 = MockMem32()
machine_mod.freq = lambda: 150_000_000

import parallel8080  # noqa: E402


class Parallel8080BusTest(unittest.TestCase):

  def setUp(self):
    self.sio = MockMem32()
    self.pin_history = []

  def _simulate_blast(self, idle, buf, count, pad):
    for i in range(count):
      high = idle | int(buf[i]) | 0x100
      low = high ^ 0x100
      self.sio[0xD0000010] = high
      self.pin_history.append(('STROBE_HIGH', self.sio.gpio_out))
      for _ in range(pad):
        self.sio[0xD0000010] = low
        self.pin_history.append(('STROBE_LOW', self.sio.gpio_out))
      self.sio[0xD0000010] = high
      self.pin_history.append(('STROBE_HIGH', self.sio.gpio_out))

  def test_data_bus_and_strobe_sequence(self):
    idle = 0
    test_data = bytes([0x42, 0xA5])
    self._simulate_blast(idle, test_data, len(test_data), pad=1)

    # First byte: 0x42
    self.assertEqual(0x142, self.pin_history[0][1] & 0x1FF)
    self.assertEqual(0x42, self.pin_history[1][1] & 0x1FF)
    self.assertEqual(0x142, self.pin_history[2][1] & 0x1FF)

    # Second byte: 0xA5
    self.assertEqual(0x1A5, self.pin_history[3][1] & 0x1FF)
    self.assertEqual(0xA5, self.pin_history[4][1] & 0x1FF)
    self.assertEqual(0x1A5, self.pin_history[5][1] & 0x1FF)

  def test_parallel_bus_pins_and_write(self):
    bus = parallel8080.ParallelBus()
    self.assertEqual(8, parallel8080._WR_PIN)
    self.assertEqual(9, parallel8080._DC_PIN)
    self.assertEqual(10, parallel8080._RST_PIN)
    self.assertEqual(11, parallel8080._CS_PIN)

    cs_dc_states = []
    parallel8080._blast = lambda idle, buf, count, pad: cs_dc_states.append(
        (bus._cs.val, bus._dc.val, count)
    )

    # Command write (dc=0)
    bus.write(b'\x15\x00\x3F', dc=0)
    self.assertEqual([(0, 0, 3)], cs_dc_states)
    self.assertEqual(1, bus._cs.val)

    # Data write (dc=1)
    cs_dc_states.clear()
    bus.write(b'\xFF\x00', dc=1)
    self.assertEqual([(0, 1, 2)], cs_dc_states)
    self.assertEqual(1, bus._cs.val)


if __name__ == '__main__':
  unittest.main()
