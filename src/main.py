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
"""Main entrypoint for Pico train display."""

import asyncio
import errno
import gc
import json
import sys
import time
import _thread

import machine
import micropython
import network
import ntptime

import config as config_module
import display
import fonts
import logging
from setup import server
import trains
import utils
import widgets


_WIFI_CONNECT = 'Connecting'
_LOADING_DEPARTURES = 'Loading train departures...'

_SETUP_WIFI_SSID = 'Pico Train Display'
_SETUP_WIFI_PASSWORD = '12345678'
_SETUP_MESSAGE = (
    'Welcome! To setup the display, join\n'
    'Wifi: {}\nPassword: {}\nThen visit http://{}'
)

_CONNECT_TIMEOUT = 15

gc.collect()


def _connect(
    ssid: str, password: str, screen: display.Display | None = None
) -> network.WLAN | None:
  """Associates with the configured network, or gives up and returns None.

  Wifi is the first link in the chain to the API and it breaks like any other:
  the network gets renamed, the password changes, the router is off. None of
  that is worth resetting the board over, because it has departures to show
  either way, so this reports failure rather than raising it.

  With no screen it retries quietly, leaving whatever is on the panel alone.
  """
  widget = (
      widgets.MessageWidget(screen, _WIFI_CONNECT, fonts.DEFAULT_FONT)
      if screen is not None
      else None
  )
  logging.log('Connecting to SSID: {} PASSWORD: {}', ssid, '*' * len(password))

  try:
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password if password else None)

    for i in range(_CONNECT_TIMEOUT):
      if wlan.isconnected():
        logging.log('Connected!')
        logging.log(wlan.ifconfig())
        return wlan

      if widget is not None:
        widget.render('{}{}'.format(_WIFI_CONNECT, '.' * (i % 4)))
        screen.flush()
      time.sleep(1)
  except Exception as e:
    # A rejected password surfaces differently on every port, and the radio
    # itself can refuse to come up. They all mean the same thing here.
    logging.log('Wifi connect failed!')
    sys.print_exception(e)
    return None

  logging.log('Failed to connect to wifi in {} secs', _CONNECT_TIMEOUT)
  return None


def _configure_time() -> bool:
  """Sets the clock from NTP, reporting whether it managed to.

  The same chain: no network means no time either. Bounded, because a board
  that cannot reach NTP still has a display to draw.
  """
  logging.log('Configure datetime.')
  for _ in range(_CONNECT_TIMEOUT):
    try:
      ntptime.settime()
      t = time.localtime()
      logging.log('Time set to UTC {}/{}/{} {}:{}', *t[:5])
      return True
    except Exception:
      time.sleep(1)

  logging.log('Failed to reach NTP in {} secs', _CONNECT_TIMEOUT)
  return False


def _render_thread(
    screen: display.Display,
    departure_updater: trains.DepartureUpdater,
    config: config_module.Config,
    main_running: _thread.LockType,
    thread_running: _thread.LockType,
):
  with thread_running:
    main_display = widgets.MainWidget(
        screen,
        departure_updater,
        fonts.DEFAULT_FONT,
        fonts.CLOCK_FONT,
        scroll_speed=config.display.scroll_speed,
    )
    refresh_rate_us = int((1 / config.display.refresh) / 1e-6)
    screen.fill(0)

    while main_running.locked():
      start = time.ticks_us()

      if main_display.render(utils.get_uk_time()):
        gc.collect()
        screen.flush()

      gc.collect()
      elapsed = time.ticks_diff(time.ticks_us(), start)
      sleep_for = refresh_rate_us - elapsed
      if sleep_for > 0:
        time.sleep_us(sleep_for)
    logging.log('Render thread closing...')


def run(config: config_module.Config):
  logging.log('Starting...')

  screen = display.create(config.display.flip)
  main_running = _thread.allocate_lock()
  thread_running = _thread.allocate_lock()
  try:
    main_running.acquire()
    departure_updater = trains.DepartureUpdater(
        config.station,
        config.destination,
        config.rtt.endpoint,
        config.rtt.token,
        min_departure_time=config.min_departure_time,
    )
    gc.collect()
    micropython.mem_info()
    gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())

    wlan = _connect(config.wifi.ssid, config.wifi.password, screen=screen)
    clock_set = _configure_time() if wlan is not None else False

    logging.log('Get initial train departures')
    widget = widgets.MessageWidget(
        screen, _LOADING_DEPARTURES, fonts.DEFAULT_FONT
    )
    widget.render()
    screen.flush()

    # Get first set of departures synchonously. A failure here is not fatal:
    # the display falls back to the departures baked into the firmware, and
    # the update loop below keeps trying.
    try:
      departure_updater.update()
    except Exception as e:
      logging.log('Initial train update failed, using fallback departures.')
      sys.print_exception(e)
    gc.collect()

    logging.log('Start render loop')
    _ = _thread.start_new_thread(
        _render_thread,
        (screen, departure_updater, config, main_running, thread_running),
    )

    update_interval = config.rtt.update_interval
    logging.log('Start updating departures every {} seconds', update_interval)
    failures = 0
    while True:
      # The whole chain, rebuilt from wherever it broke: the aerial first,
      # then the clock, then the API. Any link can be down at any point, and
      # the board keeps showing what it has while they come back.
      if wlan is None or not wlan.isconnected():
        wlan = _connect(config.wifi.ssid, config.wifi.password)
      if wlan is not None and not clock_set:
        clock_set = _configure_time()

      # One request a go, and a failure just brings the next go forward: a
      # second or two for a blip, longer each time it keeps failing. There is
      # no separate retry loop, because retrying is the same thing as going
      # round again sooner. trains.retry_wait decides how much sooner.
      try:
        departure_updater.update()
        gc.collect()
        failures = 0
        wait = update_interval
      except Exception as e:
        # Anything at all: a dropped connection, a rate limit, a revoked
        # token, an API that has been retired and now answers with something
        # we can't parse. The board keeps showing what it has either way, so
        # none of it is worth resetting the device over.
        failures += 1
        if isinstance(e, OSError) and e.errno == errno.ECONNABORTED:
          # Aborted mid-request, which can happen while still associated, so
          # the check at the top of the loop would not catch it.
          logging.log('Received ECONNABORTED error, try reconnecting...')
          wlan = _connect(config.wifi.ssid, config.wifi.password)
        wait = trains.retry_wait(e, failures, update_interval)
        logging.log(
            'Train update failed, {} in a row, waiting {}s: {}',
            failures,
            wait,
            e,
        )
        sys.print_exception(e)

      for _ in range(wait):
        time.sleep(1)
  finally:
    logging.log('Main thread closing...')
    main_running.release()

    # Wait for thread lock to be released, which indicates the thread has
    # finished running (or was never started).
    with thread_running:
      screen.close()


async def _setup_access_point():
  ap = network.WLAN(network.AP_IF)
  ap.config(ssid=_SETUP_WIFI_SSID, password=_SETUP_WIFI_PASSWORD)
  ap.active(True)
  logging.log('Creating AP wifi with SSID: {}', _SETUP_WIFI_SSID)

  # active() is the radio being up, which is all the portal needs. On an
  # access point isconnected() means a station has joined, and waiting for
  # that before showing the instructions that say which network to join is a
  # deadlock: nobody joins, this raises, and the board resets, having shown a
  # blank screen the whole time.
  for _ in range(_CONNECT_TIMEOUT):
    if ap.active():
      return ap
    await asyncio.sleep(1)

  raise OSError(
      'Failed to bring up access point in {} secs'.format(_CONNECT_TIMEOUT)
  )


async def setup(screen: display.Display):
  event = asyncio.Event()
  ap = await _setup_access_point()
  ip_address = ap.ifconfig()[0]

  setup_message = _SETUP_MESSAGE.format(
      _SETUP_WIFI_SSID, _SETUP_WIFI_PASSWORD, ip_address
  )
  logging.log(setup_message)

  widget = widgets.MessageWidget(screen, setup_message, fonts.DEFAULT_FONT)
  widget.render()
  screen.flush()

  def _write_config(cfg):
    _ = config_module.load(cfg)
    with open('config.json', 'w') as f:
      json.dump(cfg, f)

  web_server = await server.start(_write_config, event)
  await event.wait()
  web_server.close()
  screen.fill(0)
  screen.flush()
  await web_server.wait_closed()


def main():
  try:
    with open('config.json', 'r') as f:
      config = config_module.load(json.load(f))
  except OSError:
    screen = display.create()
    try:
      asyncio.run(setup(screen))
      machine.reset()
    finally:
      screen.close()

  if config.debug.log:
    logging.set_logging_file('debug.txt')
  run(config)


if __name__ == '__main__':
  try:
    main()
  except KeyboardInterrupt:
    logging.log('Keyboard interrupt!')
  except Exception as e:
    logging.log('Unhandled exception!')
    sys.print_exception(e)
    micropython.mem_info()
    raise e
  finally:
    logging.log('Shutdown')
    logging.on_exit()

    # Hard reset device to reset RAM. Although this should be unnecessary,
    # residual, fragmented memory seems to still exist.
    machine.reset()
