import time
import timeit

def loop_sleep():
    for _ in range(100):
        pass # mock time.sleep

def single_sleep():
    pass

loop_time = timeit.timeit(loop_sleep, number=100000)
single_time = timeit.timeit(single_sleep, number=100000)

print(f"Loop overhead: {loop_time:.4f}s")
print(f"Single operation overhead: {single_time:.4f}s")
print(f"Improvement: {(loop_time - single_time) / loop_time * 100:.2f}%")
