import sys
sys.path.append('src')

class _Ptr32: pass
import builtins
builtins.ptr32 = _Ptr32

import types
micropython_mod = sys.modules.setdefault('micropython', types.ModuleType('micropython'))
if not hasattr(micropython_mod, 'viper'):
    def viper(func):
        # We need a wrapper to mock `ptr32` behaviour in pure Python
        def wrapper(*args, **kwargs):
            if func.__name__ == '_find_changed_rows_viper':
                new_buf, old_buf, words_per_row, rows = args
                first = -1
                last = -1
                for r in range(rows):
                    start = r * words_per_row
                    stop = start + words_per_row
                    changed = False
                    for i in range(start, stop):
                        b_start = i * 4
                        if new_buf[b_start:b_start+4] != old_buf[b_start:b_start+4]:
                            changed = True
                            break
                    if changed:
                        if first < 0:
                            first = r
                        last = r
                if first < 0:
                    return -1
                return (first << 16) | last
            return func(*args, **kwargs)
        return wrapper
    micropython_mod.viper = viper

import ssd1322
import timeit

def original_find_changed_rows(new_buf, old_buf, row_bytes, rows):
  first = -1
  last = -1
  for r in range(rows):
    start = r * row_bytes
    stop = start + row_bytes
    if new_buf[start:stop] != old_buf[start:stop]:
      if first < 0:
        first = r
      last = r
  if first < 0:
    return None
  return first, last

buf1 = bytearray(8192)
buf2 = bytearray(8192)
buf2[4000] = 1
buf2[8000] = 1

t1 = timeit.timeit(lambda: original_find_changed_rows(buf1, buf2, 128, 64), number=1000)
t2 = timeit.timeit(lambda: ssd1322._find_changed_rows(buf1, buf2, 128, 64), number=1000)

print(f"Original: {t1:.4f}s")
print(f"Viper: {t2:.4f}s")
print(f"Improvement: {(t1 - t2) / t1 * 100:.2f}%")
