"""Tests for pi_focus_tracker.display (TextZone and LCDDisplay)."""

import time
import threading
import unittest
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Patch smbus2 before importing the module under test so that the module-level
# import inside display.py resolves to our mock.
# ---------------------------------------------------------------------------
_mock_smbus_module = MagicMock()
_mock_bus_instance = MagicMock()
_mock_smbus_module.SMBus.return_value = _mock_bus_instance


class TestTextZone(unittest.TestCase):
    """Unit tests for TextZone."""

    def _make(self, **kwargs):
        from pi_focus_tracker.display import TextZone
        defaults = dict(name="z", row=0, col=0, width=8)
        defaults.update(kwargs)
        return TextZone(**defaults)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def test_basic_construction(self):
        zone = self._make(text="hello")
        self.assertEqual(zone.name, "z")
        self.assertEqual(zone.text, "hello")
        self.assertEqual(zone.row, 0)
        self.assertEqual(zone.col, 0)
        self.assertEqual(zone.width, 8)

    def test_invalid_width(self):
        from pi_focus_tracker.display import TextZone
        with self.assertRaises(ValueError):
            TextZone("z", 0, 0, 0)

    def test_invalid_row(self):
        from pi_focus_tracker.display import TextZone
        with self.assertRaises(ValueError):
            TextZone("z", -1, 0, 8)

    def test_invalid_col(self):
        from pi_focus_tracker.display import TextZone
        with self.assertRaises(ValueError):
            TextZone("z", 0, -1, 8)

    # ------------------------------------------------------------------
    # Static zone display text
    # ------------------------------------------------------------------

    def test_static_short_text_padded(self):
        zone = self._make(width=8, text="Hi")
        self.assertEqual(zone.get_display_text(), "Hi      ")

    def test_static_exact_width(self):
        zone = self._make(width=5, text="Hello")
        self.assertEqual(zone.get_display_text(), "Hello")

    def test_static_text_truncated(self):
        zone = self._make(width=4, text="Hello")
        self.assertEqual(zone.get_display_text(), "Hell")

    def test_empty_text(self):
        zone = self._make(width=6, text="")
        self.assertEqual(zone.get_display_text(), "      ")

    # ------------------------------------------------------------------
    # Scrolling zone display text
    # ------------------------------------------------------------------

    def test_scrolling_short_text_treated_as_static(self):
        """Text that fits in the zone should not scroll."""
        zone = self._make(width=8, text="Hi", scrolling=True)
        t0 = zone.get_display_text()
        zone.tick()
        t1 = zone.get_display_text()
        self.assertEqual(t0, t1)   # no scroll happened

    def test_scrolling_initial_window(self):
        zone = self._make(width=5, text="ABCDEF", scrolling=True, scroll_speed=9999)
        self.assertEqual(zone.get_display_text(), "ABCDE")

    def test_scrolling_advances_after_tick(self):
        """After forcing a scroll step, the window should shift."""
        zone = self._make(width=4, text="ABCDE", scrolling=True, scroll_speed=0)
        # scroll_speed=0 means we scroll every time tick() is called
        before = zone.get_display_text()
        time.sleep(0.001)    # ensure monotonic advances past 0
        zone.tick()
        after = zone.get_display_text()
        self.assertNotEqual(before, after)

    def test_scrolling_wraps(self):
        """After enough ticks the text should wrap back to the start."""
        # text "ABCDE" (len 5) is longer than width 3, so scrolling activates
        zone = self._make(width=3, text="ABCDE", scrolling=True, scroll_speed=0)
        seen = set()
        for _ in range(20):
            time.sleep(0.001)
            zone.tick()
            seen.add(zone.get_display_text())
        # There should be more than one distinct window seen
        self.assertGreater(len(seen), 1)

    # ------------------------------------------------------------------
    # set_text resets scroll
    # ------------------------------------------------------------------

    def test_set_text_resets_scroll(self):
        zone = self._make(width=4, text="ABCDE", scrolling=True, scroll_speed=0)
        time.sleep(0.001)
        zone.tick()   # advance once
        zone.set_text("XY")
        self.assertEqual(zone.get_display_text(), "XY  ")

    # ------------------------------------------------------------------
    # tick() dirty flag
    # ------------------------------------------------------------------

    def test_tick_dirty_on_new_zone(self):
        """A freshly created zone should report dirty on the first tick."""
        zone = self._make(width=4, text="Hi")
        self.assertTrue(zone.tick())

    def test_tick_not_dirty_after_first_tick_static(self):
        """A static zone should not be dirty after the first tick."""
        zone = self._make(width=4, text="Hi")
        zone.tick()   # consume the initial dirty flag
        self.assertFalse(zone.tick())

    def test_set_text_marks_dirty(self):
        zone = self._make(width=4, text="Hi")
        zone.tick()
        zone.set_text("Bye")
        self.assertTrue(zone.tick())


# ---------------------------------------------------------------------------
# LCDDisplay tests (all hardware calls are mocked out)
# ---------------------------------------------------------------------------

class TestLCDDisplay(unittest.TestCase):
    """Unit tests for LCDDisplay using a mock SMBus instance."""

    def _make_display(self, **kwargs):
        """Create a display with a mock bus and auto_update off."""
        from pi_focus_tracker.display import LCDDisplay
        bus = MagicMock()
        defaults = dict(auto_update=False, _bus=bus)
        defaults.update(kwargs)
        return LCDDisplay(**defaults), bus

    # ------------------------------------------------------------------
    # Construction / init
    # ------------------------------------------------------------------

    def test_construction_does_not_raise(self):
        display, _ = self._make_display()
        self.assertIsNotNone(display)

    def test_default_update_frequency(self):
        display, _ = self._make_display()
        self.assertEqual(display.update_frequency, 0.5)

    def test_invalid_update_frequency_raises(self):
        with self.assertRaises(ValueError):
            self._make_display(update_frequency=0)

    def test_init_calls_write_byte(self):
        display, bus = self._make_display()
        # During init the LCD is initialised; write_byte should have been called
        self.assertTrue(bus.write_byte.called)

    # ------------------------------------------------------------------
    # Zone management
    # ------------------------------------------------------------------

    def test_add_zone_returns_text_zone(self):
        from pi_focus_tracker.display import TextZone
        display, _ = self._make_display()
        zone = display.add_zone("title", row=0, col=0, width=16, text="Hello")
        self.assertIsInstance(zone, TextZone)
        self.assertEqual(zone.text, "Hello")

    def test_get_zone_retrieves_zone(self):
        display, _ = self._make_display()
        display.add_zone("status", row=1, col=0, width=16, text="OK")
        zone = display.get_zone("status")
        self.assertEqual(zone.text, "OK")

    def test_remove_zone(self):
        display, _ = self._make_display()
        display.add_zone("temp", row=0, col=0, width=8)
        display.remove_zone("temp")
        with self.assertRaises(KeyError):
            display.get_zone("temp")

    def test_set_zone_text(self):
        display, _ = self._make_display()
        display.add_zone("msg", row=0, col=0, width=8, text="Old")
        display.set_zone_text("msg", "New")
        self.assertEqual(display.get_zone("msg").text, "New")

    def test_set_zone_text_unknown_zone_raises(self):
        display, _ = self._make_display()
        with self.assertRaises(KeyError):
            display.set_zone_text("nonexistent", "text")

    def test_add_zone_rejects_overlap_on_same_row(self):
        display, _ = self._make_display()
        display.add_zone("left", row=0, col=0, width=8, text="Hello")

        with self.assertRaises(ValueError):
            display.add_zone("right", row=0, col=7, width=4, text="Oops")

    def test_add_zone_allows_adjacent_zone(self):
        display, _ = self._make_display()
        display.add_zone("left", row=0, col=0, width=8, text="Hello")

        zone = display.add_zone("right", row=0, col=8, width=4, text="OK")

        self.assertEqual(zone.col, 8)

    def test_add_zone_rejects_zone_past_display_edge(self):
        display, _ = self._make_display(cols=16)

        with self.assertRaises(ValueError):
            display.add_zone("wide", row=0, col=12, width=5, text="Oops")

    def test_allows_full_width_zone(self):
        display, _ = self._make_display(cols=16)

        zone = display.add_zone("full", row=0, col=0, width=16, text="Full width")

        self.assertEqual(zone.col, 0)
        self.assertEqual(zone.width, 16)

    def test_add_zone_rejects_row_past_display_edge(self):
        display, _ = self._make_display(rows=2)

        with self.assertRaises(ValueError):
            display.add_zone("bottom", row=2, col=0, width=4, text="Oops")

    def test_add_zone_rejects_negative_col(self):
        display, _ = self._make_display()

        with self.assertRaises(ValueError):
            display.add_zone("neg", row=0, col=-1, width=4, text="Oops")

    def test_add_zone_rejects_negative_row(self):
        display, _ = self._make_display()

        with self.assertRaises(ValueError):
            display.add_zone("neg", row=-1, col=0, width=4, text="Oops")

    # ------------------------------------------------------------------
    # update() writes to bus
    # ------------------------------------------------------------------

    def test_update_writes_to_bus(self):
        display, bus = self._make_display()
        bus.reset_mock()
        display.add_zone("row0", row=0, col=0, width=5, text="Hello")
        display.update()
        # Expect write_byte calls for the DDRAM address command + character data
        self.assertTrue(bus.write_byte.called)

    def test_update_multiple_zones(self):
        display, bus = self._make_display()
        display.add_zone("a", row=0, col=0, width=8, text="AAAAAAAA")
        display.add_zone("b", row=1, col=0, width=8, text="BBBBBBBB")
        bus.reset_mock()
        display.update()
        # Both zones should have caused writes
        call_count = bus.write_byte.call_count
        self.assertGreater(call_count, 0)

    # ------------------------------------------------------------------
    # Backlight
    # ------------------------------------------------------------------

    def test_backlight_off_clears_backlight_bit(self):
        display, bus = self._make_display()
        bus.reset_mock()
        display.backlight(False)
        # At least one write_byte call should have been made
        self.assertTrue(bus.write_byte.called)
        # The backlight bit (0x08) should NOT be set in any call
        for c in bus.write_byte.call_args_list:
            data = c[0][1]
            self.assertEqual(data & 0x08, 0)

    def test_backlight_on_sets_backlight_bit(self):
        display, bus = self._make_display()
        display.backlight(False)   # turn off first
        bus.reset_mock()
        display.backlight(True)
        # At least one write should have the backlight bit set
        bits = [c[0][1] for c in bus.write_byte.call_args_list]
        self.assertTrue(any(b & 0x08 for b in bits))

    # ------------------------------------------------------------------
    # clear()
    # ------------------------------------------------------------------

    def test_clear_sends_command(self):
        display, bus = self._make_display()
        bus.reset_mock()
        display.clear()
        self.assertTrue(bus.write_byte.called)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def test_context_manager(self):
        from pi_focus_tracker.display import LCDDisplay
        bus = MagicMock()
        with LCDDisplay(auto_update=False, _bus=bus) as disp:
            disp.add_zone("x", 0, 0, 5, "Hello")
        # After exit, bus should have received the clear/backlight-off commands

    # ------------------------------------------------------------------
    # No-bus mode (smbus not available)
    # ------------------------------------------------------------------

    def test_no_bus_does_not_raise(self):
        from pi_focus_tracker.display import LCDDisplay
        display = LCDDisplay(auto_update=False, _bus=None)
        display.add_zone("z", 0, 0, 8, "test")
        display.update()   # should be a no-op

    # ------------------------------------------------------------------
    # Auto-update thread
    # ------------------------------------------------------------------

    def test_auto_update_thread_starts_and_stops(self):
        from pi_focus_tracker.display import LCDDisplay
        bus = MagicMock()
        display = LCDDisplay(auto_update=True, _bus=bus)
        self.assertTrue(display._running)
        display.stop()
        self.assertFalse(display._running)

    def test_auto_update_skips_ticks_without_active_zones(self):
        from pi_focus_tracker.display import LCDDisplay
        bus = MagicMock()
        display = LCDDisplay(auto_update=False, _bus=bus, update_frequency=0.01)
        display.update = MagicMock(wraps=display.update)

        display.start()
        time.sleep(0.05)
        display.stop()

        self.assertEqual(display.update.call_count, 0)

    def test_auto_update_ticks_when_scroll_zone_is_active(self):
        from pi_focus_tracker.display import LCDDisplay
        bus = MagicMock()
        display = LCDDisplay(auto_update=False, _bus=bus, update_frequency=0.01)
        display.update = MagicMock(wraps=display.update)
        display.add_zone("scroll", row=0, col=0, width=4, text="ABCDE", scrolling=True, scroll_speed=0)

        display.start()
        time.sleep(0.06)
        display.stop()

        self.assertGreaterEqual(display.update.call_count, 2)

    def test_start_stop_idempotent(self):
        from pi_focus_tracker.display import LCDDisplay
        bus = MagicMock()
        display = LCDDisplay(auto_update=False, _bus=bus)
        display.start()
        display.start()   # second start is a no-op
        self.assertTrue(display._running)
        display.stop()
        display.stop()    # second stop is a no-op
        self.assertFalse(display._running)


if __name__ == "__main__":
    unittest.main()
