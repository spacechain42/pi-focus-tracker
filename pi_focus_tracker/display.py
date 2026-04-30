"""
display.py
==========
I²C interface for a 2×16 HD44780-compatible character LCD driven by a
PCF8574 I²C I/O expander (the most common "I²C LCD backpack" variant).

Classes
-------
- :class:`TextZone`   – A named rectangular region that holds static or
  automatically-scrolling text.
- :class:`LCDDisplay` – Top-level controller that owns one SMBus handle and
  a collection of :class:`TextZone` objects.

Hardware notes
--------------
The PCF8574 expander maps its 8 output bits to the LCD signals as follows::

    Bit 7  Bit 6  Bit 5  Bit 4  Bit 3      Bit 2  Bit 1  Bit 0
    DB7    DB6    DB5    DB4    Backlight   E      RW     RS

All communication uses the 4-bit interface mode.

Dependencies
------------
``smbus2`` (preferred) or ``smbus`` must be installed to talk to real
hardware.  When neither is available the class still instantiates and all
write operations become no-ops, which is useful for unit testing.
"""

import threading
import time
import warnings

# ---------------------------------------------------------------------------
# Optional hardware import
# ---------------------------------------------------------------------------
try:
    import smbus2 as _smbus_mod          # type: ignore[import]
    _SMBUS_CLS = _smbus_mod.SMBus
except ImportError:
    try:
        import smbus as _smbus_mod       # type: ignore[import]
        _SMBUS_CLS = _smbus_mod.SMBus
    except ImportError:
        _smbus_mod = None                # type: ignore[assignment]
        _SMBUS_CLS = None

# ---------------------------------------------------------------------------
# HD44780 command constants
# ---------------------------------------------------------------------------
_CMD_CLEAR          = 0x01
_CMD_HOME           = 0x02
_CMD_ENTRY_MODE     = 0x04
_CMD_DISPLAY_CTRL   = 0x08
_CMD_FUNCTION_SET   = 0x20
_CMD_SET_DDRAM      = 0x80

# Entry mode flags
_ENTRY_LEFT         = 0x02

# Display control flags
_DISPLAY_ON         = 0x04

# Function-set flags
_4BIT_MODE          = 0x00
_2LINE              = 0x08
_5x8_DOTS           = 0x00

# PCF8574 → LCD pin mapping (bit positions)
_RS         = 0x01   # Register Select (0 = command, 1 = data)
_RW         = 0x02   # Read/Write      (always keep at 0 → write)
_EN         = 0x04   # Enable strobe
_BACKLIGHT  = 0x08   # Backlight control
_D4         = 0x10
_D5         = 0x20
_D6         = 0x40
_D7         = 0x80

# Row address offsets for HD44780
_ROW_OFFSETS = [0x00, 0x40, 0x14, 0x54]


# ---------------------------------------------------------------------------
# TextZone
# ---------------------------------------------------------------------------

class TextZone:
    """A named rectangular region on the LCD display.

    Parameters
    ----------
    name : str
        Unique identifier for this zone.
    row : int
        Zero-based row index (0 or 1 for a 2-line display).
    col : int
        Zero-based starting column.
    width : int
        Number of characters wide.
    text : str, optional
        Initial text content.  Defaults to an empty string.
    scrolling : bool, optional
        If ``True`` the text scrolls horizontally when it is longer than
        *width*.  Defaults to ``False``.
    scroll_speed : float, optional
        Seconds between each one-character scroll step.  Defaults to 0.3.
    """

    def __init__(
        self,
        name: str,
        row: int,
        col: int,
        width: int,
        text: str = "",
        scrolling: bool = False,
        scroll_speed: float = 0.3,
    ) -> None:
        if width < 1:
            raise ValueError("width must be at least 1")
        if row < 0:
            raise ValueError("row must be non-negative")
        if col < 0:
            raise ValueError("col must be non-negative")

        self.name = name
        self.row = row
        self.col = col
        self.width = width
        self.scrolling = scrolling
        self.scroll_speed = scroll_speed

        self._text: str = ""
        self._scroll_offset: int = 0
        self._last_scroll: float = 0.0
        self._dirty: bool = True   # True when the zone needs to be redrawn

        self.set_text(text)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def text(self) -> str:
        """The raw text assigned to this zone."""
        return self._text

    def set_text(self, text: str) -> None:
        """Set the zone's text content and reset the scroll position."""
        self._text = str(text)
        self._scroll_offset = 0
        self._last_scroll = time.monotonic()
        self._dirty = True

    def get_display_text(self) -> str:
        """Return the *width*-character string that should be written to the LCD.

        For static zones the text is left-aligned and padded (or truncated)
        to *width* characters.  For scrolling zones a sliding window is
        returned; when the text fits inside *width* it is treated as static.
        """
        if not self.scrolling or len(self._text) <= self.width:
            # Static or short-enough text: pad / truncate to width
            return self._text[: self.width].ljust(self.width)

        # Build a circular scrolling buffer: "text   text   " so the display
        # wraps cleanly.
        padded = self._text + "   "
        repeated = (padded * (self.width // len(padded) + 2))
        window = repeated[self._scroll_offset: self._scroll_offset + self.width]
        return window

    def tick(self) -> bool:
        """Advance the scroll position if enough time has passed.

        Returns
        -------
        bool
            ``True`` when the display text changed and the zone needs
            to be redrawn.
        """
        changed = self._dirty
        self._dirty = False

        if not self.scrolling or len(self._text) <= self.width:
            return changed

        now = time.monotonic()
        if now - self._last_scroll >= self.scroll_speed:
            padded_len = len(self._text) + 3   # matches "text   "
            self._scroll_offset = (self._scroll_offset + 1) % padded_len
            self._last_scroll = now
            changed = True

        return changed


# ---------------------------------------------------------------------------
# LCDDisplay
# ---------------------------------------------------------------------------

class LCDDisplay:
    """2×16 I²C character LCD controller.

    Manages a set of :class:`TextZone` objects and drives a HD44780-
    compatible LCD module connected via a PCF8574 I²C I/O expander.

    Parameters
    ----------
    i2c_address : int, optional
        7-bit I²C address of the PCF8574 expander.  Common values are
        ``0x27`` and ``0x3F``.  Defaults to ``0x27``.
    bus_number : int, optional
        SMBus/I²C bus number (``1`` on most Raspberry Pi models).
    cols : int, optional
        Number of columns on the display.  Defaults to 16.
    rows : int, optional
        Number of rows on the display.  Defaults to 2.
    update_frequency : float, optional
        Seconds between background auto-update loop iterations.
        Defaults to ``0.5``.
    auto_update : bool, optional
        When ``True`` a background thread continuously refreshes scrolling
        zones and rewrites changed zones to the LCD.  Defaults to ``True``.

    Raises
    ------
    RuntimeError
        If *i2c_bus* is ``None`` (no smbus library found) *and*
        ``auto_update`` is ``True``.  Pass ``auto_update=False`` to use
        the display without a real bus (e.g. in unit tests).
    """

    def __init__(
        self,
        i2c_address: int = 0x27,
        bus_number: int = 1,
        cols: int = 16,
        rows: int = 2,
        update_frequency: float = 0.5,
        auto_update: bool = True,
        _bus=None,   # dependency-injection for testing
    ) -> None:
        if update_frequency <= 0:
            raise ValueError("update_frequency must be greater than 0")

        self.i2c_address = i2c_address
        self.cols = cols
        self.rows = rows
        self.update_frequency = update_frequency

        self._zones: dict = {}
        self._backlight: bool = True
        self._running: bool = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

        # Resolve SMBus instance
        if _bus is not None:
            self._bus = _bus
        elif _SMBUS_CLS is not None:
            try:
                self._bus = _SMBUS_CLS(bus_number)
            except PermissionError as e:
                self._bus = None
                warnings.warn(
                    "Permission denied when accessing I2C bus. Running in headless mode.",
                    RuntimeWarning,
                )
        else:
            self._bus = None

        self._init_lcd()

        if auto_update:
            self.start()

    # ------------------------------------------------------------------
    # LCD hardware initialisation
    # ------------------------------------------------------------------

    def _write_byte(self, data: int) -> None:
        """Send one byte to the PCF8574 over I²C."""
        if self._bus is None:
            return
        backlight_bit = _BACKLIGHT if self._backlight else 0
        self._bus.write_byte(self.i2c_address, data | backlight_bit)

    def _strobe(self, data: int) -> None:
        """Toggle the Enable pin to latch the 4-bit nibble."""
        self._write_byte(data | _EN)
        time.sleep(0.0005)
        self._write_byte(data & ~_EN)
        time.sleep(0.0001)

    def _write4bits(self, data: int) -> None:
        """Send a 4-bit nibble and strobe the Enable pin."""
        self._write_byte(data)
        self._strobe(data)

    def _send(self, value: int, mode: int = 0) -> None:
        """Send an 8-bit value as two 4-bit nibbles.

        Parameters
        ----------
        value : int
            The byte to send.
        mode : int
            ``0`` for command, ``_RS`` (= 1) for character data.
        """
        high = mode | (value & 0xF0)
        low  = mode | ((value << 4) & 0xF0)
        self._write4bits(high)
        self._write4bits(low)

    def _command(self, cmd: int) -> None:
        self._send(cmd, 0)

    def _write_char(self, char: int) -> None:
        self._send(char, _RS)

    def _init_lcd(self) -> None:
        """Initialise the HD44780 in 4-bit mode."""
        time.sleep(0.05)          # Power-on delay
        # Initialise sequence (send 0x03 three times, then 0x02 to set 4-bit)
        self._write4bits(0x30)
        time.sleep(0.005)
        self._write4bits(0x30)
        time.sleep(0.001)
        self._write4bits(0x30)
        time.sleep(0.001)
        self._write4bits(0x20)   # Switch to 4-bit mode
        time.sleep(0.001)

        # Function set: 4-bit, 2 lines, 5×8 dots
        self._command(_CMD_FUNCTION_SET | _4BIT_MODE | _2LINE | _5x8_DOTS)
        # Display on, cursor off, blink off
        self._command(_CMD_DISPLAY_CTRL | _DISPLAY_ON)
        # Clear display
        self._command(_CMD_CLEAR)
        time.sleep(0.002)
        # Entry mode: increment, no shift
        self._command(_CMD_ENTRY_MODE | _ENTRY_LEFT)

    # ------------------------------------------------------------------
    # Public display API
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Clear the display and return the cursor to home."""
        with self._lock:
            self._command(_CMD_CLEAR)
            time.sleep(0.002)

    def backlight(self, on: bool = True) -> None:
        """Turn the backlight on or off."""
        with self._lock:
            self._backlight = on
            # Trigger a dummy write so the backlight bit takes effect
            self._write_byte(0x00)

    def set_cursor(self, row: int, col: int) -> None:
        """Move the LCD cursor to (*row*, *col*)."""
        with self._lock:
            addr = _ROW_OFFSETS[row % self.rows] + col
            self._command(_CMD_SET_DDRAM | addr)

    def write_string(self, row: int, col: int, text: str) -> None:
        """Write *text* directly at the given position (no zone required)."""
        with self._lock:
            addr = _ROW_OFFSETS[row % self.rows] + col
            self._command(_CMD_SET_DDRAM | addr)
            for char in text:
                self._write_char(ord(char))

    # ------------------------------------------------------------------
    # Zone management
    # ------------------------------------------------------------------

    def add_zone(
        self,
        name: str,
        row: int,
        col: int,
        width: int,
        text: str = "",
        scrolling: bool = False,
        scroll_speed: float = 0.3,
    ) -> "TextZone":
        """Create a new :class:`TextZone` and register it with the display.

        Parameters
        ----------
        name : str
            Unique identifier.  Used with :meth:`set_zone_text`.
        row, col : int
            Top-left position of the zone (zero-based).
        width : int
            Number of characters in the zone.
        text : str, optional
            Initial text.
        scrolling : bool, optional
            Enable horizontal scrolling for this zone.
        scroll_speed : float, optional
            Seconds between scroll steps.

        Returns
        -------
        TextZone
            The newly created zone.

        Raises
        ------
        ValueError
            If the zone falls outside the display bounds or overlaps an
            existing zone.
        """
        with self._lock:
            if row < 0 or row >= self.rows:
                raise ValueError("row must be within the display bounds")
            if col < 0 or col + width > self.cols:
                raise ValueError("zone must fit within the display width")

            zone_end = col + width
            for existing_zone in self._zones.values():
                if existing_zone.row != row:
                    continue
                existing_end = existing_zone.col + existing_zone.width
                if col < existing_end and existing_zone.col < zone_end:
                    raise ValueError("zone must not overlap an existing zone")

            zone = TextZone(name, row, col, width, text, scrolling, scroll_speed)
            self._zones[name] = zone
        return zone

    def remove_zone(self, name: str) -> None:
        """Remove a previously registered zone by name."""
        with self._lock:
            self._zones.pop(name, None)

    def set_zone_text(self, name: str, text: str) -> None:
        """Update the text content of a registered zone.

        Parameters
        ----------
        name : str
            Zone identifier (must have been added with :meth:`add_zone`).
        text : str
            New text.

        Raises
        ------
        KeyError
            If *name* is not a known zone.
        """
        with self._lock:
            self._zones[name].set_text(text)

    def get_zone(self, name: str) -> "TextZone":
        """Return the :class:`TextZone` with the given *name*."""
        return self._zones[name]

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def update(self) -> None:
        """Tick all zones and redraw any that have changed.

        Call this repeatedly when ``auto_update=False``, or let the
        background thread handle it automatically when ``auto_update=True``.
        """
        with self._lock:
            for zone in self._zones.values():
                if zone.tick():
                    display_text = zone.get_display_text()
                    addr = _ROW_OFFSETS[zone.row % self.rows] + zone.col
                    self._command(_CMD_SET_DDRAM | addr)
                    for char in display_text:
                        self._write_char(ord(char))

    # ------------------------------------------------------------------
    # Background update thread
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background refresh thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background refresh thread and wait for it to exit."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while self._running:
            with self._lock:
                should_update = any(
                    zone._dirty or (zone.scrolling and len(zone.text) > zone.width)
                    for zone in self._zones.values()
                )

            if should_update:
                self.update()

            time.sleep(self.update_frequency)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "LCDDisplay":
        return self

    def __exit__(self, *_) -> None:
        self.stop()
        self.clear()
        self.backlight(False)
