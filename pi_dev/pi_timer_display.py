
import time
import sys
  # allow import of pi_focus_tracker at the *parent* level for testing purposes
sys.path.append("..")

from pi_focus_tracker import LCDDisplay, CountdownTimer

def main():
    lcd = LCDDisplay()
    timer = CountdownTimer(30)
    lcd.add_zone("timer", row=0, col=0, width=6, text=str(timer.remaining_seconds()))

    while True:
        lcd.display_time(timer.get_time_remaining())
        time.sleep(1)
        timer.tick()

if __name__ == "__main__":
    main()