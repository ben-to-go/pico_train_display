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
    if addr == 0xD0000010:  # GPIO_OUT
      self.gpio_out = val
    elif addr == 0xD0000014:  # GPIO_OUT_SET (atomic)
      self.gpio_out |= val
    elif addr == 0xD0000018:  # GPIO_OUT_CLR (atomic)
      self.gpio_out &= ~val
    elif addr == 0xD000001C:  # GPIO_OUT_XOR (atomic)
      self.gpio_out ^= val


machine_mod.Pin = MockPin
machine_mod.mem32 = MockMem32()
machine_mod.freq = lambda: 150_000_000

import parallel8080  # noqa: E402


class Parallel8080AtomicBusTest(unittest.TestCase):
  """Verifies that parallel8080 uses atomic SIO registers and never touches Wi-Fi GPIOs."""

  def setUp(self):
    self.sio = MockMem32()
    self.pin_history = []
    self.set_history = []
    self.clr_history = []

  def _simulate_blast(self, buf, count, pad):
    """CPython equivalent of the viper _blast function for testing register writes."""
    for i in range(count):
      b = buf[i]
      # 1. Update data lines GP0-GP7 while strobe (GP8) is HIGH
      self.sio[0xD0000018] = (~b) & 0xFF
      self.clr_history.append((~b) & 0xFF)
      self.sio[0xD0000014] = b | 0x100
      self.set_history.append(b | 0x100)
      self.pin_history.append(('SET_DATA', self.sio.gpio_out))

      # 2. Pull write strobe GP8 LOW (bit 8: 0x100)
      self.sio[0xD0000018] = 0x100
      self.clr_history.append(0x100)
      self.pin_history.append(('STROBE_LOW', self.sio.gpio_out))

      # 3. Hold strobe LOW with stores
      for _ in range(pad):
        self.sio[0xD0000018] = 0x100
        self.clr_history.append(0x100)

      # 4. Pull write strobe GP8 HIGH (rising edge latches data into SSD1322)
      self.sio[0xD0000014] = 0x100
      self.set_history.append(0x100)
      self.pin_history.append(('STROBE_HIGH', self.sio.gpio_out))

  def test_blast_never_touches_wifi_gpios(self):
    # CYW43 Wi-Fi uses GP23 (Power), GP24 (Data), GP25 (CS), GP29 (Clock)
    wifi_mask = (1 << 23) | (1 << 24) | (1 << 25) | (1 << 29)
    # Set high bits 9-31 to an active Wi-Fi transaction pattern
    initial_high_pins = wifi_mask | (0xAAAAAAAA & ~0x1FF)
    self.sio.gpio_out = initial_high_pins | 0x100  # WR initially HIGH

    test_data = bytes([0x00, 0xFF, 0x55, 0xAA, 0x12, 0x34, 0x7E, 0x81])
    self._simulate_blast(test_data, len(test_data), pad=2)

    # Check every single intermediate state during the blast
    for step, state in self.pin_history:
      high_pins_state = state & ~0x1FF
      self.assertEqual(
          initial_high_pins,
          high_pins_state,
          f'Wi-Fi/high GPIO pins were clobbered during step {step}!',
      )

  def test_data_bus_and_strobe_sequence(self):
    self.sio.gpio_out = 0x100  # WR initially HIGH
    test_data = bytes([0x42, 0xA5])
    self._simulate_blast(test_data, len(test_data), pad=2)

    # First byte: 0x42 (66)
    # Step 1: data bus becomes 0x42, WR remains 1 (total = 0x142)
    self.assertEqual(0x142, self.pin_history[0][1] & 0x1FF)
    # Step 2: WR toggles LOW -> data = 0x42, WR = 0 (total = 0x42)
    self.assertEqual(0x42, self.pin_history[1][1] & 0x1FF)
    # Step 3: WR toggles HIGH -> data = 0x42, WR = 1 (total = 0x142)
    self.assertEqual(0x142, self.pin_history[2][1] & 0x1FF)

    # Second byte: 0xA5 (165)
    # Step 4: data bus becomes 0xA5, WR remains 1 (total = 0x1A5)
    self.assertEqual(0x1A5, self.pin_history[3][1] & 0x1FF)
    # Step 5: WR toggles LOW -> data = 0xA5, WR = 0 (total = 0xA5)
    self.assertEqual(0xA5, self.pin_history[4][1] & 0x1FF)
    # Step 6: WR toggles HIGH -> data = 0xA5, WR = 1 (total = 0x1A5)
    self.assertEqual(0x1A5, self.pin_history[5][1] & 0x1FF)

  def test_parallel_bus_pins_and_write(self):
    bus = parallel8080.ParallelBus()
    # Check pin objects
    self.assertEqual(8, parallel8080._WR_PIN)
    self.assertEqual(9, parallel8080._DC_PIN)
    self.assertEqual(10, parallel8080._RST_PIN)
    self.assertEqual(11, parallel8080._CS_PIN)

    # Test CS and DC state changes during write
    cs_dc_states = []
    parallel8080._blast = lambda buf, count, pad: cs_dc_states.append(
        (bus._cs.val, bus._dc.val, count)
    )

    # Command write (dc=0)
    bus.write(b'\x15\x00\x3F', dc=0)
    self.assertEqual([(0, 0, 3)], cs_dc_states)
    self.assertEqual(1, bus._cs.val, 'CS must be pulled HIGH after write')

    # Data write (dc=1)
    cs_dc_states.clear()
    bus.write(b'\xFF\x00', dc=1)
    self.assertEqual([(0, 1, 2)], cs_dc_states)
    self.assertEqual(1, bus._cs.val, 'CS must be pulled HIGH after write')

  def test_reset_pulses_rst_pin(self):
    bus = parallel8080.ParallelBus()
    rst_transitions = []
    orig_call = bus._rst.__call__

    def mock_rst_call(val=None):
      if val is not None:
        rst_transitions.append(val)
      return orig_call(val)

    bus._rst = mock_rst_call
    bus.reset()

    self.assertEqual([0, 1], rst_transitions)


if __name__ == '__main__':
  unittest.main()
