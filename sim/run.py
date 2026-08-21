"""Runs the firmware on a desktop, with the panel drawn in the terminal.

The project is not modified and knows nothing about this. Five modules on
MICROPYPATH stand in for what a desktop does not have, and the rest is
main.py:

  machine.py   Pin, which is only asked to remember levels
  parallel8080 the panel's bus; opening it creates the panel
  panel.py     the SSD1322 itself, decoding the driver's command stream and
               drawing what it reconstructs
  network.py   a CYW43 that is always associated
  ntptime.py   NTP, since the host clock is already right

Two things still need doing from out here: the firmware's log would scribble
over the panel, and the setup portal asks for port 80, which needs root.

  sim/run.sh    the display, at its real 256x64
"""

import sys

# The setup portal binds port 80, which a normal user may not.
_SETUP_PORT = 8088
_LOG_PATH = 'sim/out/firmware.log'


def _divert_logging():
  """Sends the firmware's log to a file, leaving the panel legible.

  Replaces where a line goes rather than logging.log itself, so that
  everything still passes through the firmware's own logging: a log shipped
  to a collector from here is the same log, formatted the same way and
  carrying the same tracebacks, as one shipped from the board.
  """
  import logging
  import os

  try:
    os.mkdir('sim/out')
  except OSError:
    pass
  log_file = open(_LOG_PATH, 'a')

  def write(msg):
    log_file.write(msg + '\n')
    log_file.flush()

  logging._write = write


def _too_small():
  """Says how the window compares with the panel, or None if it fits.

  The panel is 256 pixels across and the rendering spends a terminal cell on
  each, so it wants 256 columns and 33 rows. Most windows start narrower, and
  a narrow one used to wrap every row of pixels onto the next: 32 rows became
  128, the screen scrolled continuously, and the board came out looking shred-
  ded rather than merely cropped. Wrapping is off now, so it crops instead,
  which is worth saying once rather than leaving someone to wonder where the
  rest of the departures went.

  The size comes in from run.sh, because MicroPython has no way to ask for it.
  """
  import os
  import panel

  columns = int(os.getenv('SIM_COLS') or 0)
  rows = int(os.getenv('SIM_LINES') or 0)
  if not columns or not rows:
    return None

  # A row for the cursor to land on after the last one, so the panel is not
  # pushed up the screen by its own newline.
  wants_columns, wants_rows = panel.WIDTH, panel.HEIGHT // 2 + 1
  if columns >= wants_columns and rows >= wants_rows:
    return None

  return (
      'The window is {}x{} and the panel wants {}x{}, so it was cropped. '
      'Make the font smaller until it fits.'.format(
          columns, rows, wants_columns, wants_rows)
  )


def main():
  import asyncio
  import network

  _divert_logging()
  network.IP_ADDRESS = '127.0.0.1:{}'.format(_SETUP_PORT)
  start_server = asyncio.start_server
  asyncio.start_server = lambda cb, host, port, *a, **kw: start_server(
      cb, '127.0.0.1', _SETUP_PORT, *a, **kw)

  import main as firmware

  cramped = _too_small()

  # Clear, hide the cursor, and stop the terminal wrapping. Every row of the
  # panel is 256 cells wide, and a window narrower than that used to fold each
  # one onto the row below, which shredded the picture rather than cropping it.
  sys.stdout.write('\x1b[2J\x1b[?25l\x1b[?7l')
  try:
    firmware.main()
  except KeyboardInterrupt:
    pass
  finally:
    sys.stdout.write('\x1b[?7h\x1b[?25h\n')
    print('Firmware log: {}'.format(_LOG_PATH))
    # After the panel rather than before it, because the clear above would
    # have wiped it.
    if cramped:
      print(cramped)


main()
