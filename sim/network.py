"""Fake `network` module: pretends the CYW43 is already associated."""

STA_IF = 0
AP_IF = 1

STAT_IDLE = 0
STAT_CONNECTING = 1
STAT_WRONG_PASSWORD = -3
STAT_NO_AP_FOUND = -2
STAT_CONNECT_FAIL = -1
STAT_GOT_IP = 3

# What ifconfig() reports. The setup portal prints this as the address to visit,
# so run.py overrides it to include the port it actually listens on.
IP_ADDRESS = '127.0.0.1'


class WLAN:
  """Always-connected loopback WLAN."""

  def __init__(self, interface=STA_IF):
    self.interface = interface
    self._active = False
    self._ssid = None

  def active(self, is_active=None):
    if is_active is None:
      return self._active
    self._active = bool(is_active)
    return self._active

  def connect(self, ssid=None, password=None, **kwargs):
    self._ssid = ssid

  def disconnect(self):
    self._ssid = None

  def isconnected(self):
    return True

  def status(self, param=None):
    if param == 'rssi':
      return -55
    return STAT_GOT_IP

  def ifconfig(self, cfg=None):
    if cfg is None:
      return (IP_ADDRESS, '255.255.255.0', IP_ADDRESS, IP_ADDRESS)

  def config(self, *args, **kwargs):
    if args:
      return {'ssid': self._ssid, 'channel': 1}.get(args[0])

  def scan(self):
    return []


def hostname(name=None):
  return 'pico-train-display'


def country(code=None):
  return 'GB'
