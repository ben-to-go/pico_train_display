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
import time
import _thread

import machine
import micropython
import network

import config as config_module
import display
import fonts
import logging
from net.errors import RateLimitError
import otel
from services import ntp, wifi
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

_CONNECT_TIMEOUT = 30

# Long enough for the last log batch to reach the collector, short enough that
# a board which cannot transmit is not held up by trying. The rp2 watchdog
# tops out a little over eight seconds.
_SHUTDOWN_WATCHDOG_MS = 8000

gc.collect()


def _log_memory():
  """Reports heap free, allocated, and total capacity to stdout and logs."""
  micropython.mem_info()
  free = gc.mem_free()
  alloc = gc.mem_alloc()
  total = free + alloc
  pct = (alloc / total * 100) if total > 0 else 0
  logging.log(
      'Memory: {} free, {} allocated ({} total, {:.1f}% used)',
      free,
      alloc,
      total,
      pct,
  )


class _NeedsSetup(Exception):
  """The wifi would not connect, so the details are worth asking for again."""


class _RadioIsGone(Exception):
  """Nothing has loaded for long enough that only a reboot is left to try."""


def _arm_shutdown_watchdog():
  """Makes the reboot happen whatever the shutdown does next.

  The shutdown ships the log before resetting, which is worth a few seconds:
  the last thing a display says is the reason it stopped saying anything. But
  a radio that has stopped transmitting is exactly when that matters and
  exactly when the send does not come back, and the reset it holds up is the
  one thing that would have fixed the radio. Interrupting such a board left it
  needing a physical replug rather than rebooting.

  A watchdog cannot be stopped once started, which is what is wanted here:
  every path past this line ends in a reset anyway, so the only question is
  whether it is reached.
  """
  try:
    machine.WDT(timeout=_SHUTDOWN_WATCHDOG_MS)
  except Exception as e:
    # No watchdog on this port, or one already running. Neither is worth
    # abandoning the shutdown over, and both are worth knowing about.
    logging.log('No shutdown watchdog, so the reset is not guaranteed.')
    logging.exception(e)


def _connect(ssid: str, password: str, screen: display.Display | None = None):
  """Connects to station Wi-Fi with animated progress rendering on screen."""
  widget = (
      widgets.MessageWidget(screen, _WIFI_CONNECT, fonts.DEFAULT_FONT)
      if screen is not None
      else None
  )

  def _progress(i: int):
    if widget is not None and screen is not None:
      widget.render('{}{}'.format(_WIFI_CONNECT, '.' * (i % 4)))
      screen.flush()

  return wifi.connect(
      ssid,
      password,
      timeout=_CONNECT_TIMEOUT,
      on_progress=_progress,
      network_module=network,
  )


def _scan_networks() -> list[str]:
  """Scans for nearby Wi-Fi networks."""
  return wifi.scan_networks(network_module=network)


def _no_power_saving(wlan):
  """Disables Wi-Fi power saving mode."""
  return wifi._no_power_saving(wlan, network_module=network)


def _configure_time() -> bool:
  """Synchronizes RTC time via NTP."""
  return ntp.sync_time(timeout=_CONNECT_TIMEOUT)


def _render_thread(
    screen: display.Display,
    departure_updater: trains.DepartureUpdater,
    config: config_module.Config,
    main_running: _thread.LockType,
    thread_running: _thread.LockType,
):
  """Core 1 dedicated UI render loop."""
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
        screen.flush()

      elapsed = time.ticks_diff(time.ticks_us(), start)
      sleep_for = refresh_rate_us - elapsed
      if sleep_for > 0:
        time.sleep_us(sleep_for)
    logging.log('Render thread closing...')


def run(config: config_module.Config):
  """Happy-path orchestrator and main network update loop."""
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
    _log_memory()
    gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())

    wlan = _connect(config.wifi.ssid, config.wifi.password, screen=screen)
    if wlan is None:
      # A wrong password and a network that has moved look the same from here,
      # and both are fixed by the same screen. Asking is the only thing the
      # board can do about either, so it asks.
      raise _NeedsSetup()

    clock_set = _configure_time()

    logging.log('Get initial train departures')
    widget = widgets.MessageWidget(
        screen, _LOADING_DEPARTURES, fonts.DEFAULT_FONT
    )
    widget.render()
    screen.flush()

    update_interval = config.rtt.update_interval
    failures = 0

    # When a board was last actually loaded from the API. About the fetch
    # rather than the wifi, because the fetch is the only thing here that a
    # radio which has stopped transmitting cannot fake.
    last_loaded = time.ticks_ms()

    # Get first set of departures synchronously. A failure here is not fatal:
    # the display falls back to the departures baked into the firmware, and
    # the update loop below keeps trying.
    #
    # What it decides is when the loop below first asks for another. A board
    # that has just arrived is good for the full interval; one that failed
    # deserves the same quick second go as any other failure.
    try:
      departure_updater.update()
      wait = update_interval
      last_loaded = time.ticks_ms()
    except Exception as e:
      logging.log('Initial train update failed, using fallback departures.')
      logging.exception(e)
      failures = 1
      wait = trains.retry_wait(e, update_interval)
    gc.collect()

    logging.log('Start render loop')
    _ = _thread.start_new_thread(
        _render_thread,
        (screen, departure_updater, config, main_running, thread_running),
    )

    logging.log('Start updating departures every {} seconds', update_interval)
    while True:
      # Before the wait, not after the update below, because the wait comes
      # first now: everything logged so far is either the boot or the update
      # at the bottom of the last go round, and a display that resets during
      # a wait would otherwise take the reason down with it.
      otel.send()

      # The wait comes first, because the board on the panel has just been
      # fetched: above on the first go round, and at the bottom of this loop
      # on every one after. Waiting at the end instead spent a second request
      # on the same board a few seconds after the one that fetched it.
      for _ in range(wait):
        time.sleep(1)

      # If the link or radio is down, reboot immediately to reset hardware:
      if wlan is None or not wlan.isconnected():
        logging.error(
            'Wi-Fi connection lost, rebooting immediately to reset radio...'
        )
        raise _RadioIsGone()

      if not clock_set:
        clock_set = _configure_time()

      try:
        departure_updater.update()
        gc.collect()
        _log_memory()
        failures = 0
        wait = update_interval
        last_loaded = time.ticks_ms()
      except Exception as e:
        failures += 1
        is_socket_error = isinstance(e, OSError) and (
            e.errno in (errno.ECONNABORTED, errno.ETIMEDOUT, 103, 110, 113, 118)
        )
        if isinstance(e, RateLimitError):
          wait = max(update_interval, e.retry_after)
          logging.log('Rate limited (429), backing off for {}s...', wait)
        elif (wlan is not None and not wlan.isconnected()) or (
            is_socket_error and failures >= 3
        ):
          logging.error(
              'Network/socket error ({}, {} consecutive failures), '
              'rebooting immediately to reset radio...',
              e,
              failures,
          )
          raise _RadioIsGone()
        else:
          # Remote API server error or transient socket timeout.
          # Keep the display running (with clock + stale dot) and retry on
          # normal interval without rebooting immediately.
          wait = update_interval
          logging.error(
              'API update failed ({}, failure count {}), will retry in {}s: {}',
              type(e).__name__,
              failures,
              wait,
              e,
          )
          logging.exception(e)


  finally:
    logging.log('Main thread closing...')
    main_running.release()

    # Wait for thread lock to be released, which indicates the thread has
    # finished running (or was never started).
    with thread_running:
      screen.close()


async def setup(screen: display.Display):
  """Provisions device via captive portal Access Point web server."""
  event = asyncio.Event()
  ssids = wifi.scan_networks(network_module=network)
  ap = await wifi.setup_access_point(
      _SETUP_WIFI_SSID,
      _SETUP_WIFI_PASSWORD,
      timeout=_CONNECT_TIMEOUT,
      network_module=network,
  )
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

  web_server = await server.start(_write_config, event, ssids=ssids)
  await event.wait()

  # Give the 200 OK HTTP response a moment to flush to the client browser
  await asyncio.sleep_ms(500)

  web_server.close()
  screen.fill(0)
  screen.flush()
  await web_server.wait_closed()

  # Tear down the access point and pause so the client disassociates cleanly
  ap.active(False)
  time.sleep(1)


def _run_setup():
  """Asks for the settings, writes them, and restarts into them."""
  screen = display.create()
  try:
    asyncio.run(setup(screen))
    machine.reset()
  finally:
    screen.close()


def main():
  """Loads config, configures logging and OpenTelemetry, and launches run()."""
  try:
    with open('config.json', 'r') as f:
      config = config_module.load(json.load(f))
  except (OSError, ValueError, TypeError) as e:
    # No config, or one this firmware cannot read: a setting that has since
    # been removed, a value out of range, a file that got truncated. They all
    # leave nothing to run on, so ask for it again. Resetting instead just
    # loops, because the setup screen only appears when there is no config and
    # an unreadable one still counts as a config.
    logging.log('No usable config, starting setup.')
    logging.exception(e)
    _run_setup()
    return

  if config.debug.log:
    logging.set_logging_file('debug.txt')

  # Before run(), so that a display which never gets as far as the wifi still
  # has its side of the story to tell once it does.
  otel.install(config.otel)

  try:
    run(config)
  except _NeedsSetup:
    logging.log('Could not join the wifi, asking for the details again.')
    _run_setup()
  except _RadioIsGone:
    # Asked for, not a crash: the loop has run out of things to try and wants
    # the reset in the shutdown below. Returning gets it there without the
    # traceback and the memory dump an unhandled exception prints, neither of
    # which says anything about a radio that stopped answering.
    logging.error('Rebooting to get the radio back.')


if __name__ == '__main__':
  try:
    main()
  except KeyboardInterrupt:
    logging.log('Keyboard interrupt!')
  except Exception as e:
    logging.error('Unhandled exception!')
    logging.exception(e)
    _log_memory()
    raise e
  finally:
    logging.log('Shutdown')
    # The last thing a display says is the reason it stopped saying anything,
    # so it is worth a few seconds before the reset - but only a few, and only
    # if the send comes back at all. The watchdog goes on first so that the
    # reboot happens either way.
    _arm_shutdown_watchdog()
    otel.send()
    otel.flush_wal()
    logging.on_exit()

    # Hard reset device to reset RAM. Although this should be unnecessary,
    # residual, fragmented memory seems to still exist.
    machine.reset()
