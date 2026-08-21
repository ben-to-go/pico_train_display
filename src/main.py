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
import ntptime

import config as config_module
import display
import fonts
import logging
import otel
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

# How long a display goes without loading a board before it reboots. Nothing
# short of a fetch working says the network is there: the radio can lose the
# ability to transmit while the link still reads as up, and isconnected() goes
# on saying yes throughout. Half an hour is long enough that a router being
# rebooted or an API having a bad morning is ridden out rather than reset over.
_REBOOT_AFTER = 30 * 60

# Long enough for the last log batch to reach the collector, short enough that
# a board which cannot transmit is not held up by trying. The rp2 watchdog
# tops out a little over eight seconds.
_SHUTDOWN_WATCHDOG_MS = 8000

gc.collect()


def _log_memory():
  """Reports what memory is left, to stdout and to the log.

  mem_info() writes its map straight to stdout, where only a serial cable can
  read it. The two numbers worth watching from a distance go through the log
  as well, which is the only route off the display.
  """
  micropython.mem_info()
  logging.log('Memory: {} free, {} allocated', gc.mem_free(), gc.mem_alloc())


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


def _no_power_saving(wlan: network.WLAN):
  """Stops the radio parking itself between beacons.

  The chip comes up in CYW43_PERFORMANCE_PM, which sleeps between beacons to
  save power that a display screwed to a wall on a mains adaptor has no use
  for. It is also the mode the CYW43 has been seen to wedge in after a few
  hours: it stops granting transmit credits, the board can no longer send a
  packet, and nothing here notices, because the link still reads as up. This
  is not a fix for that, and it is not meant as one; it removes one reason for
  it to happen at no cost to a display that is never on batteries.

  Set after active(True) and before connecting, because bringing the chip up
  from cold resets this to the default: anything that cycles the radio has to
  ask again, and going through here is how it does.

  The value is read back off the chip rather than reported from the constant,
  so that the log says what the radio did rather than what it was told.
  """
  try:
    wlan.config(pm=network.WLAN.PM_NONE)
    logging.log('Wifi power saving: pm={}', wlan.config('pm'))
  except Exception as e:
    # A board that cannot turn power saving off is still a working board, so
    # this is worth saying and not worth failing the connect over: raising
    # from here would send a display that was about to work to the setup
    # screen instead.
    logging.log('Could not turn off wifi power saving.')
    logging.exception(e)


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
    _no_power_saving(wlan)
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
    logging.exception(e)
    return None

  logging.log('Failed to connect to wifi in {} secs', _CONNECT_TIMEOUT)
  return None


def _configure_time() -> bool:
  """Sets the clock from NTP, reporting whether it managed to.

  The same chain: no network means no time either. Bounded, because a board
  that cannot reach NTP still has a display to draw.
  """
  logging.log('Configure datetime.')
  last_error = None
  for _ in range(_CONNECT_TIMEOUT):
    try:
      ntptime.settime()
      t = time.localtime()
      logging.log('Time set to UTC {}/{}/{} {}:{}', *t[:5])
      return True
    except Exception as e:
      # Kept rather than logged, because fifteen of these say no more than
      # the last one does. Without the clock nothing else is stamped right,
      # so what stopped it is worth having.
      last_error = e
      time.sleep(1)

  logging.log('Failed to reach NTP in {} secs: {}', _CONNECT_TIMEOUT, last_error)
  if last_error is not None:
    logging.exception(last_error)
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

    # Get first set of departures synchonously. A failure here is not fatal:
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
      wait = trains.retry_wait(e, failures, update_interval)
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

        # Checked inside the wait rather than once a go round, because the
        # backoff reaches half an hour, which is the deadline itself: a board
        # waiting one out would not look at this until an hour had gone.
        #
        # A reboot is the whole of the recovery. Taking the interface down
        # first looks gentler but cannot work: active(False) sends a
        # disassociate ioctl over the same bus that is carrying nothing, and
        # connect() has the same problem with its join. Dropping power to the
        # chip is what clears it, and a reset is how this board does that.
        stale_for = time.ticks_diff(time.ticks_ms(), last_loaded) // 1000
        if stale_for >= _REBOOT_AFTER:
          logging.log(
              'Nothing has loaded for {}s, so rebooting to get the radio '
              'back.', stale_for)
          raise _RadioIsGone()

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
        last_loaded = time.ticks_ms()
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
        logging.exception(e)

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


def _run_setup():
  """Asks for the settings, writes them, and restarts into them."""
  screen = display.create()
  try:
    asyncio.run(setup(screen))
    machine.reset()
  finally:
    screen.close()


def main():
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
    logging.log('Rebooting to get the radio back.')


if __name__ == '__main__':
  try:
    main()
  except KeyboardInterrupt:
    logging.log('Keyboard interrupt!')
  except Exception as e:
    logging.log('Unhandled exception!')
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
    logging.on_exit()

    # Hard reset device to reset RAM. Although this should be unnecessary,
    # residual, fragmented memory seems to still exist.
    machine.reset()
