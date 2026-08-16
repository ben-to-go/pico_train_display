"""Stands in for the 8080 parallel bus, which a desktop has no GPIO for.

The real bus pokes the RP2's GPIO output register from viper, which is not
something the unix port can be made to fake. So this replaces the electrical
layer only: the driver above it is the real one, and the bytes handed over are
the same bytes, in the same order, with the same data/command split. Opening
the bus is what brings the panel into being, the way opening the SPI bus used
to be.

That leaves the wiring itself unexercised, which is the one thing only the
hardware can tell you.
"""


class ParallelBus:
  """The bus as the driver sees it: bytes, and whether they are commands."""

  def __init__(self):
    import panel

    self._panel = panel.Panel()
    self.bytes_written = 0

  def reset(self):
    pass

  def write(self, buf, dc: int):
    self.bytes_written += len(buf)
    self._panel.write(bytes(buf), dc)
