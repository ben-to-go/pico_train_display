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
"""Wi-Fi subsystem management: connection, scanning, recovery, and AP setup."""

import asyncio
import time

try:
  import network
except ImportError:
  import sys
  # For desktop test runs or simulator where network mock exists
  network = sys.modules.get('network')

import logging

_DEFAULT_TIMEOUT = 15


def _no_power_saving(wlan):
  """Turns power saving off on chips with CYW43439-style PM modes."""
  try:
    wlan.config(pm=network.WLAN.PM_NONE)
    logging.log('Wifi power saving: pm={}', wlan.config('pm'))
  except Exception as e:
    logging.log('Could not turn off wifi power saving.')
    logging.exception(e)


def wifi_status_desc(status: int) -> str:
  """Returns a human-readable description for a CYW43 Wi-Fi status code."""
  statuses = {
      0: 'STAT_IDLE',
      1: 'STAT_CONNECTING',
      2: 'STAT_WRONG_PASSWORD',
      3: 'STAT_GOT_IP',
      -1: 'STAT_CONNECT_FAIL',
      -2: 'STAT_NO_AP_FOUND',
      -3: 'STAT_WRONG_PASSWORD',
  }
  return statuses.get(status, str(status))


def connect(
    ssid: str,
    password: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    on_progress=None,
    screen=None,
):
  """Associates with the configured Wi-Fi network, returning WLAN on success."""
  logging.log('Connecting to SSID: {} PASSWORD: {}', ssid, '*' * len(password))

  try:
    if hasattr(network, 'AP_IF'):
      ap = network.WLAN(network.AP_IF)
      if ap.active():
        ap.active(False)

    wlan = network.WLAN(network.STA_IF)
    wlan.active(False)
    wlan.active(True)
    _no_power_saving(wlan)
    wlan.connect(ssid, password if password else None)

    for i in range(timeout):
      if wlan.isconnected():
        ifc = wlan.ifconfig()
        logging.log(
            'Connected! IP: {}, Gateway: {}, DNS: {}', ifc[0], ifc[2], ifc[3]
        )
        return wlan

      if wlan.status() < 0:
        logging.log(
            'Wifi link failure: status={} ({}), retrying...',
            wlan.status(),
            wifi_status_desc(wlan.status()),
        )
        try:
          wlan.disconnect()
        except Exception:
          pass
        time.sleep_ms(200)
        wlan.connect(ssid, password if password else None)

      if on_progress is not None:
        on_progress(i)
      time.sleep(1)
  except Exception as e:
    logging.log('Wifi connect failed!')
    logging.exception(e)
    return None

  logging.log(
      'Failed to connect to wifi in {} secs: status={} ({})',
      timeout,
      wlan.status(),
      wifi_status_desc(wlan.status()),
  )
  return None


def scan_networks() -> list[str]:
  """Finds nearby Wi-Fi network SSIDs, sorted by signal strength."""
  try:
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    results = sta.scan()
    ssids = []
    seen = set()
    for s in sorted(
        results, key=lambda x: x[3] if len(x) > 3 else 0, reverse=True
    ):
      ssid_raw = s[0]
      if isinstance(ssid_raw, (bytes, bytearray)):
        name = ssid_raw.decode('utf-8', 'ignore').strip()
      else:
        name = str(ssid_raw).strip()
      if name and name not in seen:
        seen.add(name)
        ssids.append(name)
    return ssids
  except Exception as e:
    logging.log('Could not scan for wifi networks.')
    logging.exception(e)
    return []


async def setup_access_point(
    ssid: str, password: str, timeout: int = _DEFAULT_TIMEOUT
):
  """Brings up an Access Point for configuration portal provisioning."""
  ap = network.WLAN(network.AP_IF)
  ap.config(ssid=ssid, password=password)
  ap.active(True)
  logging.log('Creating AP wifi with SSID: {}', ssid)

  for _ in range(timeout):
    if ap.active():
      return ap
    await asyncio.sleep(1)

  raise OSError(
      'Failed to bring up access point in {} secs'.format(timeout)
  )
