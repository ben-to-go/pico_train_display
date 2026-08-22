import os
import re
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_ROOT, 'src')
_MANIFEST = os.path.join(_ROOT, 'manifest.py')


class ManifestTest(unittest.TestCase):
  """Ensures all source modules and packages are declared in manifest.py."""

  def test_all_src_python_modules_are_frozen_in_manifest(self):
    with open(_MANIFEST, 'r') as f:
      manifest_content = f.read()

    frozen_modules = set(
        re.findall(r"module\(['\"]([^'\"]+)['\"],\s*base_path=['\"]src['\"]\)", manifest_content)
    )
    frozen_packages = set(
        re.findall(r"package\(['\"]([^'\"]+)['\"],\s*base_path=['\"]src['\"]\)", manifest_content)
    )

    for entry in os.listdir(_SRC):
      path = os.path.join(_SRC, entry)
      if os.path.isdir(path):
        if entry not in ('__pycache__',):
          self.assertIn(
              entry,
              frozen_packages,
              f"Package 'src/{entry}' is missing from manifest.py! Add package('{entry}', base_path='src')",
          )
      elif entry.endswith('.py'):
        self.assertIn(
            entry,
            frozen_modules,
            f"Module 'src/{entry}' is missing from manifest.py! Add module('{entry}', base_path='src')",
        )


if __name__ == '__main__':
  unittest.main()
