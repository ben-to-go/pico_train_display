import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401

fb_mod = sys.modules.setdefault('framebuf', types.ModuleType('framebuf'))
if not hasattr(fb_mod, 'FrameBuffer'):
  class MockFrameBuffer:
    def __init__(self, *args, **kwargs):
      self.blits = []
    def blit(self, src, x, y, key=-1, palette=None):
      self.blits.append((src, x, y))
  fb_mod.FrameBuffer = MockFrameBuffer
if not hasattr(fb_mod, 'MONO_HLSB'):
  fb_mod.MONO_HLSB = 1
  fb_mod.GS8 = 2
  fb_mod.GS4_HMSB = 3

if 'uctypes' not in sys.modules:
  uc_mod = types.ModuleType('uctypes')
  uc_mod.addressof = lambda buf: 0
  uc_mod.bytearray_at = lambda addr, length: bytearray(length)
  sys.modules['uctypes'] = uc_mod

sys.modules.pop('fonts', None)
import fonts


class MockGlyphFont:
  def min_ch(self):
    return 32  # space
  def max_ch(self):
    return 126  # tilde
  def height(self):
    return 8
  def max_width(self):
    return 6
  def get_ch(self, char):
    return (bytearray(8), 8, 5)


class MockTargetFrameBuffer:
  def __init__(self):
    self.blits = []
  def blit(self, src, x, y, key=-1, palette=None):
    self.blits.append((src, x, y))


class FontsTest(unittest.TestCase):

  def test_font_renders_ascii_text_normally(self):
    mock_font = MockGlyphFont()
    palette = sys.modules['framebuf'].FrameBuffer()
    font = fonts.Font(mock_font, palette)
    target_fb = MockTargetFrameBuffer()

    w, h = font.calculate_bounds('Stoke Mandeville')
    self.assertEqual(16 * 5, w)
    self.assertEqual(8, h)

    font.render_text('Stoke Mandeville', target_fb, 10, 20)
    self.assertEqual(16, len(target_fb.blits))

  def test_font_handles_out_of_bounds_characters_safely(self):
    mock_font = MockGlyphFont()
    palette = sys.modules['framebuf'].FrameBuffer()
    font = fonts.Font(mock_font, palette)
    target_fb = MockTargetFrameBuffer()

    # Characters with ord > 126 or ord < 32
    text_with_unicode = 'St Pancras Int\u2019l \u2014 Caf\u00e9 \u2022 \u00a35 \u2192 London'
    w, h = font.calculate_bounds(text_with_unicode)
    self.assertGreater(w, 0)
    self.assertEqual(8, h)

    # Should not raise IndexError
    font.render_text(text_with_unicode, target_fb, 0, 0)
    valid_chars = sum(1 for c in text_with_unicode if 32 <= ord(c) <= 126)
    self.assertEqual(valid_chars, len(target_fb.blits))


if __name__ == '__main__':
  unittest.main()
