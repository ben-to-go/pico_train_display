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
"""Configuration class for storing config options."""

import os

def _token_from_env() -> str | None:
  """RTT_TOKEN from the environment, if there is one.

  Lets the token stay out of config.json while developing. The device has no
  environment to read, and os.getenv only exists on ports that do, so this is
  always None on hardware.
  """
  getenv = getattr(os, 'getenv', None)
  return getenv('RTT_TOKEN') if getenv else None


def from_firmware(name: str) -> str | None:
  """A token built in by tools/write_baked.py, if this build had one.

  How a display given away as a present has an API token without anyone typing
  one into the setup page. Read last, so config.json still wins. Nothing
  generates that module outside a firmware build, and a build with no .env
  writes empty strings, so finding nothing is the ordinary case.
  """
  try:
    import baked
  except ImportError:
    return None
  return getattr(baked, name, None) or None


class RttConfig:
  """Real-time trains configuration."""

  def __init__(self, endpoint: str, token: str, update_interval: int):
    self.endpoint = endpoint
    self.token = token or _token_from_env() or from_firmware('RTT_TOKEN')
    self.update_interval = update_interval

  def validate(self):
    if not self.token:
      raise ValueError('RTT API token must be set!')
    if self.update_interval <= 0:
      raise ValueError(
          f'RTT update interval must be > 0! {self.update_interval=}'
      )


def _auth_from_headers(headers: str | None) -> str | None:
  """The Authorization value out of an OTEL_EXPORTER_OTLP_HEADERS string.

  'name=value,name=value', of which only the one header is any use here, and
  _normalise_auth tidies up what it says. Its own function because that string
  now arrives from the environment or from the firmware, and one reader of it
  is what stops a header working in the simulator and failing on the board.
  """
  for header in (headers or '').split(','):
    name, _, value = header.partition('=')
    if name.strip().lower() == 'authorization':
      return value
  return None


def _otel_from_env() -> tuple[str | None, str | None]:
  """(endpoint, auth) from the standard OTEL_ variables, if they are set.

  The same trick as _token_from_env, and for the same reason: the simulator
  reads .env, which is where the OpenTelemetry variables Grafana hands out
  already live. The device has no environment and gets these from
  config.json, or from what was baked into the firmware, instead.
  """
  getenv = getattr(os, 'getenv', None)
  if getenv is None:
    return None, None

  return (
      getenv('OTEL_EXPORTER_OTLP_ENDPOINT'),
      _auth_from_headers(getenv('OTEL_EXPORTER_OTLP_HEADERS')),
  )


def _normalise_auth(auth: str) -> str:
  """An Authorization header value, however it was pasted in.

  Grafana shows this as the environment variable wants it, name and all, with
  the space in 'Basic <token>' written as %20. Pasting that into the setup
  page is the obvious thing to do, and sends a header the gateway answers with
  401 "no credentials provided", which says nothing about which of the two
  ends is wrong. So every way of writing it is accepted: with the name or
  without, escaped or not, and the token on its own.
  """
  auth = auth.strip().replace('%20', ' ')
  if auth.lower().startswith('authorization'):
    auth = auth[len('authorization'):].lstrip(' =:')
  # No space means a token with no scheme in front of it. Basic is the only
  # one Grafana Cloud issues, and a bearer token would say so itself.
  if auth and ' ' not in auth:
    auth = 'Basic ' + auth
  return auth


# Somewhere for the endpoint to point by default, so that turning the log
# collector on is a matter of pasting one token rather than two settings.
DEFAULT_OTEL_ENDPOINT = 'https://otlp-gateway-prod-gb-south-1.grafana.net/otlp'


class OtelConfig:
  """Where to send the log, on top of stdout.

  Entirely optional, and off until it has an auth header to send: an endpoint
  on its own is just an address nothing is posted to.
  """

  def __init__(self, endpoint: str = '', auth: str = ''):
    env_endpoint, env_auth = _otel_from_env()
    baked_auth = _auth_from_headers(from_firmware('OTEL_HEADERS'))
    # The base OTLP endpoint, without the /v1/logs that otel.py appends.
    self.endpoint = endpoint or env_endpoint or DEFAULT_OTEL_ENDPOINT
    # The whole Authorization header value, 'Basic <base64>' for Grafana
    # Cloud, tidied up so that pasting what the console shows works.
    self.auth = _normalise_auth(auth or env_auth or baked_auth or '')

  @property
  def enabled(self) -> bool:
    return bool(self.endpoint and self.auth)

  def validate(self):
    # Nothing to check. Either setting may be left out, and leaving one out is
    # how the log stays on the display, so there is no half-filled state worth
    # refusing a whole config over.
    pass


def _parse_known_wifi(raw: str | list | None) -> list[tuple[str, str]]:
  """Parses known Wi-Fi networks from a string (comma-separated or JSON) or list."""
  if not raw:
    return []
  if isinstance(raw, list):
    networks = []
    for item in raw:
      if isinstance(item, dict):
        ssid = item.get('ssid', '')
        pw = item.get('password', '')
        if ssid:
          networks.append((str(ssid).strip(), str(pw).strip()))
      elif isinstance(item, (tuple, list)) and len(item) >= 2:
        networks.append((str(item[0]).strip(), str(item[1]).strip()))
    return networks

  if isinstance(raw, str):
    raw = raw.strip()
    if raw.startswith('[') and raw.endswith(']'):
      try:
        import json
        return _parse_known_wifi(json.loads(raw))
      except Exception:
        pass
    networks = []
    for entry in raw.split(','):
      entry = entry.strip()
      if not entry:
        continue
      if ':' in entry:
        ssid, pw = entry.split(':', 1)
      else:
        ssid, pw = entry, ''
      if ssid.strip():
        networks.append((ssid.strip(), pw.strip()))
    return networks

  return []


class WifiConfig:
  """WiFi configuration supporting multiple known networks and baked-in credentials."""

  def __init__(
      self,
      ssid: str = '',
      password: str = '',
  ):
    getenv = getattr(os, 'getenv', None)
    env_wifi = getenv('KNOWN_WIFI') if getenv else None
    baked_wifi = from_firmware('KNOWN_WIFI')

    explicit_networks = []
    if ssid:
      explicit_networks.append((ssid.strip(), password.strip()))

    fallback_networks = _parse_known_wifi(env_wifi or baked_wifi)

    seen = set()
    combined = []
    for s, p in explicit_networks + fallback_networks:
      if s and s not in seen:
        seen.add(s)
        combined.append((s, p))

    self.networks = tuple(combined)
    self.ssid = self.networks[0][0] if self.networks else ''
    self.password = self.networks[0][1] if self.networks else ''

  def validate(self):
    pass


class DisplayConfig:
  """Display configuration."""

  def __init__(
      self,
      refresh: int,
      flip: bool = False,
      scroll_speed: int = 60,
  ):
    self.refresh = refresh
    self.flip = flip
    # Pixels a second, so it does not depend on refresh.
    self.scroll_speed = scroll_speed

  def validate(self):
    if self.refresh <= 0:
      raise ValueError(f'Display refresh must be > 0! refresh={self.refresh}')
    if self.scroll_speed <= 0:
      raise ValueError(
          f'Display scroll speed must be > 0! scroll_speed={self.scroll_speed}'
      )
    if not isinstance(self.flip, bool):
      raise ValueError(f'Display flip must be a boolean! flip={self.flip}')


class DebugConfig:
  """Debug configuration."""

  def __init__(self, log: bool = False):
    self.log = log

  def validate(self):
    if not isinstance(self.log, bool):
      raise ValueError(f'Debug log must be a boolean! log={self.log}')


class Config:
  """Main configuration class."""

  def __init__(
      self,
      *,
      destination: str,
      station: str,
      wifi: WifiConfig,
      rtt: RttConfig,
      display: DisplayConfig,
      min_departure_time: int = 0,
      debug: DebugConfig = DebugConfig(),
      otel: OtelConfig = OtelConfig(),
  ):
    self.destination = destination
    self.station = station
    self.wifi = wifi
    self.rtt = rtt
    self.display = display
    self.min_departure_time = min_departure_time
    self.debug = debug
    self.otel = otel
    self.validate()

  def validate(self):
    if len(self.destination) != 3:
      raise ValueError(f'Invalid destination! destination={self.destination}')
    if len(self.station) != 3:
      raise ValueError(f'Invalid station! station={self.station}')
    self.wifi.validate()
    self.rtt.validate()
    self.display.validate()
    if self.min_departure_time < 0:
      raise ValueError(
          'Minimum departure time must be >= 0! '
          f'min_departure_time={self.min_departure_time}'
      )
    self.debug.validate()
    self.otel.validate()


def load(config_json) -> Config:
  kwargs = {}
  for k, v in config_json.items():
    if k == 'wifi':
      if isinstance(v, dict):
        wifi_obj = WifiConfig(
            ssid=v.get('ssid', ''),
            password=v.get('password', ''),
        )
        if 'networks' in v:
          extra_nets = _parse_known_wifi(v['networks'])
          seen = set()
          merged = []
          for s, p in extra_nets + list(wifi_obj.networks):
            if s and s not in seen:
              seen.add(s)
              merged.append((s, p))
          wifi_obj.networks = tuple(merged)
          wifi_obj.ssid = wifi_obj.networks[0][0] if wifi_obj.networks else ''
          wifi_obj.password = (
              wifi_obj.networks[0][1] if wifi_obj.networks else ''
          )
        kwargs[k] = wifi_obj
      else:
        kwargs[k] = v
    elif k == 'display':
      kwargs[k] = DisplayConfig(**v)
    elif k == 'rtt':
      kwargs[k] = RttConfig(**v)
    elif k == 'debug':
      kwargs[k] = DebugConfig(**v)
    elif k == 'otel':
      kwargs[k] = OtelConfig(**v)
    else:
      kwargs[k] = v
  return Config(**kwargs)

