
import time
import sys
  # allow import of pi_focus_tracker at the *parent* level for testing purposes
sys.path.append("..")

from pi_focus_tracker import LCDDisplay, CountdownTimer, TimerState, Button

def main():
    lcd = LCDDisplay()
    pause_dummy = Button(17)
    end_dummy = Button(22)
    lcd.add_zone("label", row=1, col=0, width=16, text="Time Remaining")
    timer = CountdownTimer(lcd, "timer", 10, pause_dummy, end_dummy)

    timer.start()
    try:
        while timer.state == TimerState.RUNNING:
            time.sleep(1)
            lcd.update()
    except KeyboardInterrupt:
        pass
    finally:
        timer.stop()
        lcd.clear()

if __name__ == "__main__":
    main()
