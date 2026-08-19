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
"""What the board does when the wifi will not connect at startup.

It asks for the settings again, because a wrong password and a network that
has moved look the same from here and the same screen fixes both. Only at
startup: once it is running, a network that drops out is left to the refetch
loop, which carries on showing the departures it has.

Importing main on a desktop takes some arranging, since it expects a Pico.
Worth it, because otherwise nothing but a Pico ever runs this code.

Run with:
  python3 -m unittest discover -s tests
"""

import json
import os
import _thread as _thread_module
import shutil
import sys
import tempfile
import types
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), '..')
_REPLACED = {name: sys.modules.get(name)
             for name in ('logging', 'asyncio', 'micropython', 'framebuf',
                          'uctypes')}


def _stub(name, **attrs):
  module = types.ModuleType(name)
  for key, value in attrs.items():
    setattr(module, key, value)
  sys.modules[name] = module


class _FrameBuffer:
  """Enough of framebuf for the font modules to load."""

  def __init__(self, *args, **kwargs):
    pass

  def __getattr__(self, _):
    return lambda *args, **kwargs: None


# main imports asyncio, and the real one imports the standard library's
# logging, which src/logging.py shadows as soon as src is on the path. None of
# these tests run the setup coroutine, so a stand-in avoids all of that.
_stub('asyncio', run=lambda *a: None, sleep=lambda *a: None,
      start_server=lambda *a, **k: None, Event=object, Server=object,
      StreamReader=object, StreamWriter=object)
_stub('micropython', const=lambda v: v, mem_info=lambda *a: None,
      viper=lambda f: f)
_stub('framebuf', FrameBuffer=_FrameBuffer, GS4_HMSB=0, GS8=1, MONO_HLSB=2)
_stub('uctypes', addressof=lambda x: 0, bytearray_at=lambda *a: b'',
      struct=lambda *a: None, UINT8=0, UINT16=1, ARRAY=2, LITTLE_ENDIAN=0)

# So that `import logging` finds src/logging.py rather than a cached standard
# library one.
sys.modules.pop('logging', None)
sys.path.insert(0, os.path.join(_ROOT, 'sim'))
sys.path.insert(0, os.path.join(_ROOT, 'src'))

# MicroPython's sys has this and CPython's does not. main uses it to report
# what went wrong without taking the board down with it.
if not hasattr(sys, 'print_exception'):
  sys.print_exception = lambda e, *a: None

import main  # noqa: E402


def tearDownModule():
  """Puts sys.modules back, so the stand-ins cannot reach the other tests."""
  for name, module in _REPLACED.items():
    if module is None:
      sys.modules.pop(name, None)
    else:
      sys.modules[name] = module


_VALID_CONFIG = {
    'station': 'SKM', 'destination': 'MYB',
    'wifi': {'ssid': 'net', 'password': 'pw'},
    'rtt': {'endpoint': 'https://data.rtt.io', 'token': 'token',
            'update_interval': 120},
    'display': {'refresh': 60, 'flip': False, 'scroll_speed': 15},
    'debug': {'log': False},
}


class _GotPastTheWifi(Exception):
  """Raised from the step after the wifi check, to show it was reached."""


class WifiAtStartupTest(unittest.TestCase):

  def setUp(self):
    for name in ('gc', 'display', 'trains', '_connect', '_configure_time'):
      self.addCleanup(setattr, main, name, getattr(main, name))

    main.gc = types.SimpleNamespace(
        collect=lambda: None, threshold=lambda *a: None,
        mem_free=lambda: 1, mem_alloc=lambda: 1)
    main.display = types.SimpleNamespace(
        create=lambda *a, **k: types.SimpleNamespace(
            close=lambda: None, fill=lambda *a: None, flush=lambda: None))
    main.trains = types.SimpleNamespace(DepartureUpdater=lambda *a, **k: None)

  def _config(self):
    return main.config_module.load(_VALID_CONFIG)

  def test_no_wifi_asks_for_the_settings(self):
    main._connect = lambda *a, **k: None

    with self.assertRaises(main._NeedsSetup):
      main.run(self._config())

  def test_wifi_connecting_carries_on(self):
    # The other direction, which is what stops this being a board that always
    # asks. run() never returns, so the step after the check raises to show
    # that it was reached.
    main._connect = lambda *a, **k: object()

    def past():
      raise _GotPastTheWifi()

    main._configure_time = past

    with self.assertRaises(_GotPastTheWifi):
      main.run(self._config())


class SetupIsOfferedTest(unittest.TestCase):
  """main() reads config.json from the working directory, so give it one."""

  def setUp(self):
    for name in ('run', '_run_setup'):
      self.addCleanup(setattr, main, name, getattr(main, name))
    self.setup_ran = []
    main._run_setup = lambda: self.setup_ran.append(True)

    directory = tempfile.mkdtemp()
    self.addCleanup(shutil.rmtree, directory)
    self.addCleanup(os.chdir, os.getcwd())
    os.chdir(directory)

  def _write_config(self, config):
    with open('config.json', 'w') as f:
      json.dump(config, f)

  def test_setup_when_the_wifi_will_not_connect(self):
    self._write_config(_VALID_CONFIG)

    def refuses(config):
      raise main._NeedsSetup()

    main.run = refuses
    main.main()

    self.assertEqual([True], self.setup_ran)

  def test_setup_when_the_config_cannot_be_read(self):
    self._write_config({'station': 'SKM'})
    main.run = lambda config: self.fail('should not have run')

    main.main()

    self.assertEqual([True], self.setup_ran)


if __name__ == '__main__':
  unittest.main()


class _Stop(BaseException):
  """Ends run()'s loop from inside it.

  Not an Exception, so that the loop's own handling of a failed update does
  not catch it and carry on.
  """


class RequestsAtStartupTest(unittest.TestCase):
  """One board fetched at startup, not two.

  The loop used to wait at the end of its body, so its first request landed a
  few seconds after the one that had just filled the panel: a request spent
  on the board already showing, every time the display was switched on. The
  API allows a hundred an hour and a display that keeps resetting pays this
  each time round.
  """

  def setUp(self):
    for name in ('gc', 'display', 'trains', 'widgets', 'fonts', 'otel',
                 'time', '_thread', '_connect', '_configure_time'):
      self.addCleanup(setattr, main, name, getattr(main, name))

    self.updates = 0
    self.slept = 0
    self.order = []
    test = self

    class _Updater:

      def __init__(self, *args, **kwargs):
        pass

      def update(self):
        test.updates += 1
        test.order.append('fetch')
        if test.updates == 2:
          raise _Stop()

    def sleep(seconds):
      test.slept += seconds
      test.order.append('wait')

    def send():
      test.order.append('send')

    real_lock = _thread_module.allocate_lock
    main.gc = types.SimpleNamespace(
        collect=lambda: None, threshold=lambda *a: None,
        mem_free=lambda: 1, mem_alloc=lambda: 1)
    main.display = types.SimpleNamespace(
        create=lambda *a, **k: types.SimpleNamespace(
            close=lambda: None, fill=lambda *a: None, flush=lambda: None))
    main.widgets = types.SimpleNamespace(
        MessageWidget=lambda *a, **k: types.SimpleNamespace(
            render=lambda *a, **k: None))
    # Nothing here draws, and which fonts exist depends on which test module
    # arranged the framebuf stand-in first.
    main.fonts = types.SimpleNamespace(
        DEFAULT_FONT=object(), CLOCK_FONT=object())
    main.trains = types.SimpleNamespace(
        DepartureUpdater=_Updater, retry_wait=lambda e, n, interval: 1)
    main.otel = types.SimpleNamespace(send=send, install=lambda c: None)
    main.time = types.SimpleNamespace(sleep=sleep)
    main._thread = types.SimpleNamespace(
        allocate_lock=real_lock, start_new_thread=lambda *a, **k: None)
    # An associated radio, which the loop rechecks every time round.
    main._connect = lambda *a, **k: types.SimpleNamespace(
        isconnected=lambda: True)
    main._configure_time = lambda: True

  def _run(self):
    with self.assertRaises(_Stop):
      main.run(main.config_module.load(_VALID_CONFIG))

  def test_asks_once_and_then_waits_before_asking_again(self):
    self._run()

    self.assertEqual(2, self.updates, 'stopped on the second, by design')
    self.assertEqual(120, self.slept,
                     'a full interval between the two, not a few seconds')

  def test_the_boot_is_shipped_before_the_first_wait(self):
    # The reason this loop waits first at all is to save a request, and the
    # send used to sit after the update at the bottom. Left there it ships
    # nothing for a whole interval, so a display switched on and watched looks
    # like one that is not shipping at all, and one that resets inside that
    # interval takes the reason down with it.
    self._run()

    self.assertLess(self.order.index('send'), self.order.index('wait'),
                    self.order[:4])

  def test_the_boot_is_shipped_after_the_board_it_describes(self):
    # The other side of it: sending before the startup fetch would leave its
    # three requests and whatever they said for the go round after.
    self._run()

    self.assertEqual(['fetch', 'send', 'wait', 'fetch'],
                     [step for i, step in enumerate(self.order)
                      if i == 0 or step != self.order[i - 1]])

  def test_a_failed_first_board_is_retried_like_any_other_failure(self):
    # The other direction: waiting first must not turn a blip at startup into
    # two minutes of the departures baked into the firmware.
    failed = []

    class _FailsFirst:

      def __init__(self, *args, **kwargs):
        pass

      def update(self):
        failed.append(1)
        if len(failed) == 1:
          raise OSError('no route to host')
        raise _Stop()

    main.trains = types.SimpleNamespace(
        DepartureUpdater=_FailsFirst, retry_wait=lambda e, n, interval: 1)

    self._run()


    self.assertEqual(1, self.slept, 'the backoff, not the whole interval')
