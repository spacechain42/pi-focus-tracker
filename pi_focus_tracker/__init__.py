"""
pi_focus_tracker
================
Raspberry Pi hardware interface library for a 2×16 I²C character display
and GPIO push-buttons.  Includes a :class:`Controls` orchestrator that can
run external scripts or call Python callables based on individual button
presses, button combinations, or press-and-hold events.

Public API
----------
- :class:`~pi_focus_tracker.display.TextZone`
- :class:`~pi_focus_tracker.display.LCDDisplay`
- :class:`~pi_focus_tracker.button.Button`
- :class:`~pi_focus_tracker.controls.Controls`
"""

from .display import LCDDisplay, TextZone
from .button import Button
from .controls import Controls

__all__ = ["LCDDisplay", "TextZone", "Button", "Controls"]
