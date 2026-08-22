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
  network = None


import logging

_DEFAULT_TIMEOUT = 15


def _no_power_saving(wlan, network_module=None) -> None:
  """Turns power saving off on chips with CYW43439-style PM modes."""
  net = network_module if network_module is not None else network
  try:
    wlan.config(pm=net.WLAN.PM_NONE)
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
    network_module=None,
):
  """Associates with the configured Wi-Fi network, returning WLAN on success."""
  net = network_module if network_module is not None else network
  logging.log('Connecting to SSID: {} PASSWORD: {}', ssid, '*' * len(password))

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
        logging.log(
            'Connected! IP: {}, Gateway: {}, DNS: {}',
            ifc[0],
            ifc[2],
            ifc[3],
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
        if hasattr(time, 'sleep_ms'):
          time.sleep_ms(200)
        else:
          time.sleep(0.2)
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


def connect_known(
    known_networks: tuple[tuple[str, str], ...] | list[tuple[str, str]],
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    on_progress=None,
    network_module=None,
):
  """Connects to the best available known Wi-Fi network.

  Scans for visible SSIDs first and attempts to connect to available known
  networks in order of signal strength. If no known networks are visible or
  the scan fails, falls back to attempting known networks directly.
  """
  if not known_networks:
    return None

  known_dict = {ssid: password for ssid, password in known_networks if ssid}
  if not known_dict:
    return None

  # 1. Scan for nearby visible networks
  visible_ssids = scan_networks(network_module=network_module)

  # 2. Pick candidates that are both visible and known, in scan signal order
  candidates = [
      (ssid, known_dict[ssid]) for ssid in visible_ssids if ssid in known_dict
  ]

  if candidates:
    logging.log(
        'Found {} known Wi-Fi network(s) in scan: {}',
        len(candidates),
        ', '.join(ssid for ssid, _ in candidates),
    )
    for ssid, password in candidates:
      wlan = connect(
          ssid,
          password,
          timeout=timeout,
          on_progress=on_progress,
          network_module=network_module,
      )
      if wlan is not None:
        return wlan

    # All visible candidates failed to connect (e.g. bad password)
    return None

  # 3. If scan returned nothing (e.g. scan unsupported or radio off), try known networks directly
  if not visible_ssids:
    logging.log(
        'Wi-Fi scan returned no networks; trying known networks directly...'
    )
    for ssid, password in known_networks:
      if not ssid:
        continue
      wlan = connect(
          ssid,
          password,
          timeout=timeout,
          on_progress=on_progress,
          network_module=network_module,
      )
      if wlan is not None:
        return wlan

  # 4. If scan found networks but NONE of our known networks, log and return None
  logging.log(
      'None of {} known Wi-Fi network(s) found in scan.',
      len(known_dict),
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
    logging.log('Could not scan for wifi networks.')
    logging.exception(e)
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
  logging.log('Creating AP wifi with SSID: {}', ssid)

  for _ in range(timeout):
    if ap.active():
      return ap
    await asyncio.sleep(1)

  raise OSError(
      'Failed to bring up access point in {} secs'.format(timeout)
  )

