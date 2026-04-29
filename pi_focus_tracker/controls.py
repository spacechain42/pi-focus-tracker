"""
controls.py
===========
High-level orchestrator that connects :class:`~pi_focus_tracker.button.Button`
objects to actions (Python callables or shell commands/scripts).

Overview
--------
:class:`Controls` accepts a mapping of *name → Button* and lets you register
actions for:

1. **Individual button press** – fires when a single button becomes pressed.
2. **Button combination press** – fires when every button in a named set is
   pressed at the same time.
3. **Press-and-hold** – fires once per hold cycle when a button (or every
   button in a set) has been held for its configured *hold_time*.

Actions can be:

- A **Python callable** (called with no arguments), or
- A **string** treated as a shell command / path to a script, which is
  launched in a subprocess.

Usage example::

    from pi_focus_tracker import Button, Controls

    ok  = Button(17)
    esc = Button(27)

    ctrl = Controls({"ok": ok, "esc": esc})

    ctrl.register_press("ok",           lambda: print("OK pressed"))
    ctrl.register_press(["ok", "esc"],  "/home/pi/scripts/reset.sh")
    ctrl.register_hold("esc",           lambda: print("ESC held"))

    try:
        ctrl.run()          # blocking loop; Ctrl-C to quit
    finally:
        ctrl.stop()

"""

import shlex
import subprocess
import threading
import time
from typing import Callable, Dict, FrozenSet, List, Optional, Union

from .button import Button

# A "combo key" is an immutable set of button names.
_ComboKey = FrozenSet[str]
_Action   = Union[Callable, str]


class Controls:
    """Polls a set of :class:`~pi_focus_tracker.button.Button` objects and
    dispatches registered press, combination, and hold actions.

    Parameters
    ----------
    buttons : dict[str, Button]
        Mapping of *name* → :class:`~pi_focus_tracker.button.Button`.
    poll_interval : float, optional
        Seconds between each polling cycle.  Defaults to ``0.02`` (50 Hz).
    """

    def __init__(
        self,
        buttons: Dict[str, Button],
        poll_interval: float = 0.02,
    ) -> None:
        if not buttons:
            raise ValueError("buttons dict must not be empty")
        self._buttons: Dict[str, Button] = dict(buttons)
        self._poll_interval: float = poll_interval

        # Registered actions stored by combo key
        self._press_actions:  Dict[_ComboKey, List[_Action]] = {}
        self._hold_actions:   Dict[_ComboKey, List[_Action]] = {}

        # Hold-action de-duplication: track whether we already fired the hold
        # action for a given combo in the current hold cycle.
        self._hold_fired: Dict[_ComboKey, bool] = {}

        self._running: bool = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Action registration
    # ------------------------------------------------------------------

    def register_press(
        self,
        buttons: Union[str, List[str]],
        action: _Action,
    ) -> None:
        """Register an action that fires when *buttons* are all pressed.

        Parameters
        ----------
        buttons : str or list of str
            A single button name or a list of button names.  When all
            named buttons are simultaneously pressed (i.e. every button's
            :attr:`~pi_focus_tracker.button.Button.just_pressed` or
            :attr:`~pi_focus_tracker.button.Button.pressed` state is active
            at the same poll cycle) the action is executed.
        action : callable or str
            Python callable (called with no arguments) or a shell command
            string.

        Raises
        ------
        KeyError
            If any button name is not in the *buttons* dict passed at
            construction time.
        """
        key = self._make_key(buttons)
        self._press_actions.setdefault(key, []).append(action)

    def register_hold(
        self,
        buttons: Union[str, List[str]],
        action: _Action,
    ) -> None:
        """Register an action that fires once when *buttons* are all held.

        The action fires the first time every named button has been
        continuously pressed for at least its configured *hold_time*.  It
        will not fire again until at least one of the buttons is released and
        re-pressed.

        Parameters
        ----------
        buttons : str or list of str
            Single button name or list of button names.
        action : callable or str
            Python callable or shell command string.
        """
        key = self._make_key(buttons)
        self._hold_actions.setdefault(key, []).append(action)
        self._hold_fired[key] = False

    # ------------------------------------------------------------------
    # Main polling loop
    # ------------------------------------------------------------------

    def poll(self) -> None:
        """Run a single poll cycle.

        - Updates every button's state.
        - Checks for newly-pressed combos and fires their actions.
        - Checks for held combos and fires their actions (once per hold).

        Call this in your own loop when you want manual control over timing,
        or use :meth:`run` / :meth:`start` for an automatic background loop.
        """
        # 1. Update all button states
        for btn in self._buttons.values():
            btn.update()

        # 2. Collect currently-pressed and just-pressed button sets
        pressed_set = frozenset(n for n, b in self._buttons.items() if b.pressed)
        just_pressed_set = frozenset(n for n, b in self._buttons.items() if b.just_pressed)

        # 3. Press actions ─ fire when every button in the combo is currently
        #    pressed AND at least one became just_pressed this cycle (so the
        #    action fires exactly once per press event, not every poll).
        for key, actions in self._press_actions.items():
            if key.issubset(pressed_set) and key & just_pressed_set:
                for action in actions:
                    self._execute(action)

        # 4. Hold actions ─ fire once when every button in the combo is held
        for key, actions in self._hold_actions.items():
            all_held = key.issubset(pressed_set) and all(
                self._buttons[n].held for n in key
            )
            if all_held and not self._hold_fired.get(key, False):
                self._hold_fired[key] = True
                for action in actions:
                    self._execute(action)

            # Reset the de-duplication flag when the combo is broken
            if not key.issubset(pressed_set):
                self._hold_fired[key] = False

    def run(self, stop_event: Optional[threading.Event] = None) -> None:
        """Block the calling thread, polling buttons until stopped.

        Parameters
        ----------
        stop_event : threading.Event, optional
            When provided, the loop exits once the event is set.  If omitted
            the loop runs until :meth:`stop` is called or a
            :exc:`KeyboardInterrupt` is raised.
        """
        self._running = True
        try:
            while self._running:
                if stop_event is not None and stop_event.is_set():
                    break
                self.poll()
                time.sleep(self._poll_interval)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False

    def start(self) -> None:
        """Start the polling loop in a background daemon thread."""
        if self._running:
            return
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the polling loop to stop and wait for it to exit."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_key(self, buttons: Union[str, List[str]]) -> _ComboKey:
        """Validate and normalise *buttons* into a :class:`frozenset` key."""
        if isinstance(buttons, str):
            names = [buttons]
        else:
            names = list(buttons)
        for name in names:
            if name not in self._buttons:
                raise KeyError(f"Unknown button: {name!r}")
        return frozenset(names)

    @staticmethod
    def _execute(action: _Action) -> None:
        """Execute *action* (callable or shell command string).

        String actions are split with :func:`shlex.split` and executed via
        :class:`subprocess.Popen` without ``shell=True`` to prevent shell
        injection vulnerabilities.
        """
        if callable(action):
            action()
        elif isinstance(action, str):
            subprocess.Popen(shlex.split(action))

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "Controls":
        return self

    def __exit__(self, *_) -> None:
        self.stop()
