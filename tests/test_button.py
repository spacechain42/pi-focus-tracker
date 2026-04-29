"""Tests for pi_focus_tracker.button (Button)."""

import time
import threading
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# We patch RPi.GPIO at the module level so that importing button.py does not
# fail on non-Raspberry Pi machines.
# ---------------------------------------------------------------------------
_mock_gpio = MagicMock()
_mock_gpio.BCM = 11
_mock_gpio.IN = 1
_mock_gpio.PUD_UP = 22
_mock_gpio.PUD_DOWN = 21
_mock_gpio.HIGH = 1
_mock_gpio.LOW = 0


def _make_button(**kwargs):
    """Helper: create a Button with RPi.GPIO mocked out."""
    with patch.dict("sys.modules", {"RPi": MagicMock(), "RPi.GPIO": _mock_gpio}):
        from pi_focus_tracker import button as btn_mod
        btn_mod.GPIO = _mock_gpio
        btn_mod._HAS_GPIO = True
        B = btn_mod.Button
        defaults = dict(pin=17)
        defaults.update(kwargs)
        return B(**defaults), btn_mod


class TestButtonConstruction(unittest.TestCase):

    def test_valid_construction(self):
        btn, _ = _make_button(pin=17)
        self.assertEqual(btn.pin, 17)

    def test_invalid_pin(self):
        with self.assertRaises(ValueError):
            _make_button(pin=-1)

    def test_invalid_hold_time(self):
        with self.assertRaises(ValueError):
            _make_button(pin=17, hold_time=0)

    def test_initial_state(self):
        btn, _ = _make_button(pin=17)
        self.assertFalse(btn.pressed)
        self.assertFalse(btn.just_pressed)
        self.assertFalse(btn.just_released)
        self.assertFalse(btn.held)


class TestButtonStateTransitions(unittest.TestCase):
    """Test press / release / hold state logic using _inject_state."""

    def setUp(self):
        self.btn, _ = _make_button(pin=17, hold_time=0.1)

    def test_press_sets_pressed(self):
        self.btn.update(_inject_state=True)
        self.assertTrue(self.btn.pressed)
        self.assertTrue(self.btn.just_pressed)
        self.assertFalse(self.btn.just_released)

    def test_release_clears_pressed(self):
        self.btn.update(_inject_state=True)
        self.btn.update(_inject_state=False)
        self.assertFalse(self.btn.pressed)
        self.assertFalse(self.btn.just_pressed)
        self.assertTrue(self.btn.just_released)

    def test_just_pressed_only_on_rising_edge(self):
        self.btn.update(_inject_state=True)
        self.assertTrue(self.btn.just_pressed)
        self.btn.update(_inject_state=True)   # still pressed, not a new press
        self.assertFalse(self.btn.just_pressed)

    def test_just_released_only_on_falling_edge(self):
        self.btn.update(_inject_state=True)
        self.btn.update(_inject_state=False)
        self.assertTrue(self.btn.just_released)
        self.btn.update(_inject_state=False)  # still released
        self.assertFalse(self.btn.just_released)

    def test_held_false_before_hold_time(self):
        self.btn.update(_inject_state=True)
        self.assertFalse(self.btn.held)   # not enough time has passed

    def test_held_true_after_hold_time(self):
        self.btn.update(_inject_state=True)
        time.sleep(0.15)   # wait longer than hold_time=0.1
        self.assertTrue(self.btn.held)

    def test_held_false_when_not_pressed(self):
        self.assertFalse(self.btn.held)   # never pressed


class TestButtonCallbacks(unittest.TestCase):

    def setUp(self):
        self.btn, _ = _make_button(pin=17, hold_time=0.05)
        self.events = []

    def test_on_press_callback(self):
        self.btn.on_press(lambda: self.events.append("press"))
        self.btn.update(_inject_state=True)
        self.assertIn("press", self.events)

    def test_on_release_callback(self):
        self.btn.on_release(lambda: self.events.append("release"))
        self.btn.update(_inject_state=True)
        self.btn.update(_inject_state=False)
        self.assertIn("release", self.events)

    def test_on_hold_callback_fires_once(self):
        self.btn.on_hold(lambda: self.events.append("hold"))
        self.btn.update(_inject_state=True)
        time.sleep(0.08)
        self.btn.update(_inject_state=True)   # should fire hold
        time.sleep(0.02)
        self.btn.update(_inject_state=True)   # should NOT fire again
        hold_events = [e for e in self.events if e == "hold"]
        self.assertEqual(len(hold_events), 1)

    def test_on_hold_resets_after_release(self):
        self.btn.on_hold(lambda: self.events.append("hold"))
        # First hold cycle
        self.btn.update(_inject_state=True)
        time.sleep(0.08)
        self.btn.update(_inject_state=True)
        # Release and press again
        self.btn.update(_inject_state=False)
        self.btn.update(_inject_state=True)
        time.sleep(0.08)
        self.btn.update(_inject_state=True)
        hold_events = [e for e in self.events if e == "hold"]
        self.assertEqual(len(hold_events), 2)

    def test_multiple_press_callbacks(self):
        self.btn.on_press(lambda: self.events.append("p1"))
        self.btn.on_press(lambda: self.events.append("p2"))
        self.btn.update(_inject_state=True)
        self.assertIn("p1", self.events)
        self.assertIn("p2", self.events)

    def test_press_callback_not_called_when_already_pressed(self):
        self.btn.on_press(lambda: self.events.append("press"))
        self.btn.update(_inject_state=True)
        self.btn.update(_inject_state=True)
        self.assertEqual(self.events.count("press"), 1)


class TestButtonHeadlessMode(unittest.TestCase):
    """Button should work without RPi.GPIO present."""

    def test_headless_never_pressed(self):
        # Import button module without mocking GPIO (simulate missing library)
        import importlib
        import sys

        # Ensure RPi.GPIO is absent for this test
        modules_backup = {}
        for key in list(sys.modules.keys()):
            if "RPi" in key:
                modules_backup[key] = sys.modules.pop(key)

        try:
            import importlib as _il
            # Remove cached module to force re-import without GPIO
            if "pi_focus_tracker.button" in sys.modules:
                del sys.modules["pi_focus_tracker.button"]
            if "pi_focus_tracker" in sys.modules:
                del sys.modules["pi_focus_tracker"]

            from pi_focus_tracker.button import Button, _HAS_GPIO
            btn = Button(pin=17)
            btn.update()   # should not raise
            self.assertFalse(btn.pressed)
        finally:
            # Restore modules
            sys.modules.update(modules_backup)

    def test_cleanup_without_gpio(self):
        from pi_focus_tracker import button as bmod
        original_has_gpio = bmod._HAS_GPIO
        bmod._HAS_GPIO = False
        try:
            btn = bmod.Button(pin=17)
            btn.cleanup()   # should not raise
        finally:
            bmod._HAS_GPIO = original_has_gpio


class TestButtonContextManager(unittest.TestCase):

    def test_context_manager(self):
        btn, mod = _make_button(pin=17)
        with btn:
            pass   # exit should call cleanup without error


if __name__ == "__main__":
    unittest.main()
