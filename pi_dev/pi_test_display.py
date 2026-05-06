"""Hardware display testing script for development and debugging.  Not intended for end-users."""

import time
import sys
  # allow import of pi_focus_tracker at the *parent* level for testing purposes
sys.path.append("..")

from pi_focus_tracker import LCDDisplay

def main():
    display = LCDDisplay()
    display.add_zone("zone1", row=0, col=0, width=16, text="Hello, World!", scrolling=True)
    display.add_zone("zone2", row=1, col=0, width=16, text="Pi Focus Tracker", scrolling=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        display.clear()

if __name__ == "__main__":
    main()