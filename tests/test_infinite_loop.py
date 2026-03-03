
import time

def test_infinite_loop():
    while True:
        time.sleep(0.1)
    assert True # This will never be reached
