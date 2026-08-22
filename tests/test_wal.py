import os
import tempfile
import unittest

import wal


class WalTest(unittest.TestCase):

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.path = os.path.join(self.temp_dir.name, 'test_wal.json')

  def tearDown(self):
    self.temp_dir.cleanup()

  def test_save_and_load(self):
    entries = [
        [1700000000, 'INFO', 'Starting up', 'run12345'],
        [1700000005, 'ERROR', 'Wi-Fi lost', 'run12345'],
    ]
    wal.save(entries, path=self.path)
    loaded = wal.load(path=self.path)
    self.assertEqual(entries, loaded)

  def test_clear_removes_file(self):
    wal.save([[100, 'INFO', 'test', 'run1']], path=self.path)
    self.assertTrue(os.path.exists(self.path))

    wal.clear(path=self.path)
    self.assertFalse(os.path.exists(self.path))
    self.assertEqual([], wal.load(path=self.path))

  def test_load_nonexistent_file_returns_empty_list(self):
    self.assertEqual([], wal.load(path=self.path))

  def test_load_corrupt_json_returns_empty_list(self):
    with open(self.path, 'w') as f:
      f.write('{corrupt json')
    self.assertEqual([], wal.load(path=self.path))

  def test_load_non_list_json_returns_empty_list(self):
    with open(self.path, 'w') as f:
      f.write('{"not": "a list"}')
    self.assertEqual([], wal.load(path=self.path))

  def test_save_empty_list_clears_file(self):
    wal.save([[100, 'INFO', 'test', 'run1']], path=self.path)
    self.assertTrue(os.path.exists(self.path))

    wal.save([], path=self.path)
    self.assertFalse(os.path.exists(self.path))


if __name__ == '__main__':
  unittest.main()
