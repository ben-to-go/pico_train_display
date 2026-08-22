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
import sys

_DEFAULT_TIMEOUT = 15


def _log(msg: str, *args) -> None:
  mod = sys.modules.get('logging', logging)
  mod.log(msg, *args)


def _log_exc(e: Exception) -> None:
  mod = sys.modules.get('logging', logging)
  if hasattr(mod, 'exception'):
    mod.exception(e)


def _no_power_saving(wlan, network_module=None) -> None:
  """Turns power saving off on chips with CYW43439-style PM modes."""
  net = network_module if network_module is not None else network
  try:
    wlan.config(pm=net.WLAN.PM_NONE)
    _log('Wifi power saving: pm={}', wlan.config('pm'))
  except Exception as e:
    _log('Could not turn off wifi power saving.')
    _log_exc(e)


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
    network_module=None,
):
  """Associates with the configured Wi-Fi network, returning WLAN on success."""
  net = network_module if network_module is not None else network
  _log('Connecting to SSID: {} PASSWORD: {}', ssid, '*' * len(password))

  try:
    if hasattr(net, 'AP_IF'):
      ap = net.WLAN(net.AP_IF)
      if ap.active():
        ap.active(False)

    wlan = net.WLAN(net.STA_IF)
    wlan.active(False)
    wlan.active(True)
    _no_power_saving(wlan, network_module=net)
    wlan.connect(ssid, password if password else None)

    for i in range(timeout):
      if wlan.isconnected():
        ifc = wlan.ifconfig()
        _log('Connected! IP: {}, Gateway: {}, DNS: {}', ifc[0], ifc[2], ifc[3])
        return wlan

      if wlan.status() < 0:
        _log(
            'Wifi link failure: status={} ({}), retrying...',
            wlan.status(),
            wifi_status_desc(wlan.status()),
        )
        try:
          wlan.disconnect()
        except Exception:
          pass
        if hasattr(time, 'sleep_ms'):
          time.sleep_ms(200)
        else:
          time.sleep(0.2)
        wlan.connect(ssid, password if password else None)

      if on_progress is not None:
        on_progress(i)
      time.sleep(1)
  except Exception as e:
    _log('Wifi connect failed!')
    _log_exc(e)
    return None

  logging.log(
      'Failed to connect to wifi in {} secs: status={} ({})',
      timeout,
      wlan.status(),
      wifi_status_desc(wlan.status()),
  )
  return None


def scan_networks(network_module=None) -> list[str]:
  """Finds nearby Wi-Fi network SSIDs, sorted by signal strength."""
  net = network_module if network_module is not None else network
  try:
    sta = net.WLAN(net.STA_IF)
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
    _log('Could not scan for wifi networks.')
    _log_exc(e)
    return []


async def setup_access_point(
    ssid: str,
    password: str,
    timeout: int = _DEFAULT_TIMEOUT,
    network_module=None,
):
  """Brings up an Access Point for configuration portal provisioning."""
  net = network_module if network_module is not None else network
  ap = net.WLAN(net.AP_IF)
  ap.config(ssid=ssid, password=password)
  ap.active(True)
  _log('Creating AP wifi with SSID: {}', ssid)

  for _ in range(timeout):
    if ap.active():
      return ap
    await asyncio.sleep(1)

  raise OSError(
      'Failed to bring up access point in {} secs'.format(timeout)
  )
