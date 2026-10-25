"""Steering wheel + pedals (MOZA R3 and other DirectInput wheels) and gamepads,
via winmm.

No third-party package: the Windows multimedia joystick API (``joyGetPosEx``)
is read through ctypes. PitHouse-configured wheels show up there as an ordinary
joystick with up to 6 axes (X Y Z R U V), a POV hat and 32 buttons.

Mapping lives in ``ai_sw/wheel.json`` (written with defaults on first use; edit
it or run ``python tools/wheel_probe.py`` to see which axis is which):

    {"steer": "X", "throttle": "Z", "brake": "R",
     "invert": {"throttle": false, "brake": false, "steer": false},
     "deadzone": 0.02, "range": 1.0, "buttons": {"handbrake": 1, ...}}

A Nintendo Switch Pro Controller (or any gamepad: Xbox, PlayStation) is
recognised by its USB vendor id and gets the gamepad profile instead: left stick
steers, ZR accelerates, ZL brakes (ramped, they are buttons), see ``PAD_*``
below. A wheel is preferred if both are connected.

Pedals are calibrated automatically: the lowest and highest value each pedal
has shown since start-up are its released / pressed ends (``rest`` in the json
pins the released end if the pedal starts pressed).
"""
from __future__ import annotations

import ctypes
import json
import math
import sys
import time
from ctypes import wintypes
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "wheel.json"
AXES = ("X", "Y", "Z", "R", "U", "V")
DEFAULT = {
    "device": 0,
    "steer": "X", "throttle": "Z", "brake": "R",
    "invert": {"steer": False, "throttle": False, "brake": False},
    "deadzone": 0.02,
    "steer_range": 1.0,          # fraction of full lock that gives full steer
    "buttons": {"handbrake": 1},  # game action -> button index
    "pedal_rest": {},            # e.g. {"throttle": 0.0} to pin the released end
    "leds": False,               # drive the wheel RPM LEDs (close Pit House first)
    "pad": {                     # gamepad profile (Switch Pro Controller layout)
        "steer": "X", "stick_deadzone": 0.10, "stick_curve": 1.5,
        "throttle_button": 7, "brake_button": 6, "handbrake_button": 0,
        "ramp_up": 5.0, "ramp_down": 12.0,   # per second, for the ZR / ZL buttons
    },
}

#: USB vendor ids of gamepads: Nintendo, Microsoft, Sony, 8BitDo.
PAD_VENDORS = {0x057E, 0x045E, 0x054C, 0x2DC8}

_JOY_ALL = 0xFF


class JOYINFOEX(ctypes.Structure):
    _fields_ = [(n, wintypes.DWORD) for n in (
        "dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos", "dwRpos", "dwUpos",
        "dwVpos", "dwButtons", "dwButtonNumber", "dwPOV", "dwReserved1",
        "dwReserved2")]


class JOYCAPSW(ctypes.Structure):
    _fields_ = [("mid", wintypes.WORD), ("pid", wintypes.WORD),
                ("name", ctypes.c_wchar * 32)] + [
        (n, wintypes.UINT) for n in (
            "xmin", "xmax", "ymin", "ymax", "zmin", "zmax", "nb", "pmin", "pmax",
            "rmin", "rmax", "umin", "umax", "vmin", "vmax", "caps", "axes",
            "nax", "nbtn")] + [("regkey", ctypes.c_wchar * 32),
                               ("oemvxd", ctypes.c_wchar * 260)]


_ATTR = {"X": "dwXpos", "Y": "dwYpos", "Z": "dwZpos",
         "R": "dwRpos", "U": "dwUpos", "V": "dwVpos"}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT))
    try:
        user = json.loads(CONFIG_PATH.read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except (OSError, ValueError):
        try:
            CONFIG_PATH.write_text(json.dumps(DEFAULT, indent=2))
        except OSError:
            pass
    return cfg


class Wheel:
    """Poll with ``read()`` once per frame; ``connected`` says whether a
    device answered, ``kind`` is "wheel" or "gamepad"."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.connected = False
        self.kind = "wheel"
        self.raw: dict[str, float] = {}      # axes 0..1 as the OS reports them
        self.buttons = 0
        self.pov = -1
        self._lo: dict[str, float] = {}
        self._hi: dict[str, float] = {}
        self._winmm = ctypes.windll.winmm if sys.platform == "win32" else None
        self._dev = None
        self._info = JOYINFOEX()
        self._info.dwSize = ctypes.sizeof(JOYINFOEX)
        self._info.dwFlags = _JOY_ALL
        self._next_scan = 0
        self._t = None
        self._thr = self._brk = 0.0

    def _poll(self, dev: int) -> bool:
        return self._winmm.joyGetPosEx(dev, ctypes.byref(self._info)) == 0

    def _kind_of(self, dev: int) -> str:
        caps = JOYCAPSW()
        if (self._winmm.joyGetDevCapsW(dev, ctypes.byref(caps), ctypes.sizeof(caps)) == 0
                and caps.mid in PAD_VENDORS):
            return "gamepad"
        return "wheel"

    def _scan(self) -> None:
        """Pick the device to read: a wheel if there is one, else a gamepad."""
        best = None
        for d in range(16):
            if self._poll(d):
                kind = self._kind_of(d)
                if best is None or (kind == "wheel" and best[1] == "gamepad"):
                    best = (d, kind)
        if best is None:
            self._dev = None
        elif best[0] != self._dev:
            self._dev, self.kind = best
            self._lo.clear()
            self._hi.clear()

    def read(self) -> bool:
        if self._winmm is None:
            return False
        self._next_scan -= 1
        if self._dev is None or self._next_scan <= 0:
            # hot-plug: look through the slots now and then
            self._next_scan = 120 if self._dev is None else 240
            self._scan()
        if self._dev is None or not self._poll(self._dev):
            self.connected = False
            self._dev = None
            return False
        self.connected = True
        i = self._info
        self.raw = {a: getattr(i, _ATTR[a]) / 65535.0 for a in AXES}
        self.buttons = i.dwButtons
        self.pov = -1 if i.dwPOV == 0xFFFF else i.dwPOV // 100
        now = time.monotonic()
        dt = 0.0 if self._t is None else min(0.1, now - self._t)
        self._t = now
        if self.kind == "gamepad":
            pad = self.cfg["pad"]
            for attr, key in (("_thr", "throttle_button"), ("_brk", "brake_button")):
                tgt = 1.0 if self.buttons >> int(pad[key]) & 1 else 0.0
                cur = getattr(self, attr)
                rate = pad["ramp_up"] if tgt > cur else pad["ramp_down"]
                setattr(self, attr, cur + max(-rate * dt, min(rate * dt, tgt - cur)))
        return True

    # -- processed values -------------------------------------------------
    def _pedal(self, action: str) -> float:
        axis = self.cfg[action]
        v = self.raw.get(axis, 0.0)
        if self.cfg["invert"].get(action):
            v = 1.0 - v
        rest = self.cfg["pedal_rest"].get(action)
        lo = self._lo[action] = min(self._lo.get(action, v), v)
        hi = self._hi[action] = max(self._hi.get(action, v), v)
        if rest is not None:
            lo = rest
        span = hi - lo
        if span < 0.25:        # not moved yet: do not amplify noise
            return 0.0
        x = (v - lo) / span
        return 0.0 if x < self.cfg["deadzone"] else min(1.0, x)

    def stick(self, axis: str) -> float:
        """A gamepad stick, -1..1, dead zone removed."""
        v = (self.raw.get(axis, 0.5) - 0.5) * 2.0
        dz = self.cfg["pad"]["stick_deadzone"]
        if abs(v) < dz:
            return 0.0
        return math.copysign((abs(v) - dz) / (1.0 - dz), v)

    @property
    def steer(self) -> float:
        if self.kind == "gamepad":
            v = self.stick(self.cfg["pad"]["steer"])
            return math.copysign(min(1.0, abs(v)) ** self.cfg["pad"]["stick_curve"], v)
        v = (self.raw.get(self.cfg["steer"], 0.5) - 0.5) * 2.0
        if self.cfg["invert"].get("steer"):
            v = -v
        v /= max(self.cfg["steer_range"], 1e-3)
        dz = self.cfg["deadzone"]
        if abs(v) < dz:
            return 0.0
        return max(-1.0, min(1.0, v))

    @property
    def throttle(self) -> float:
        return self._thr if self.kind == "gamepad" else self._pedal("throttle")

    @property
    def brake(self) -> float:
        return self._brk if self.kind == "gamepad" else self._pedal("brake")

    def pressed(self, action: str) -> bool:
        if self.kind == "gamepad":
            idx = self.cfg["pad"].get(action + "_button")
        else:
            idx = self.cfg["buttons"].get(action)
        return idx is not None and bool(self.buttons >> int(idx) & 1)


#: wheel button index -> the key it presses (menus, camera, pause...).
KEY_BUTTONS = {0: "enter", 2: "x", 3: "tab", 12: "gear down", 13: "gear up",
               4: "up arrow", 5: "right arrow", 6: "down arrow", 7: "left arrow",
               24: "escape"}

#: Hold CAM (21) and press another button for the keys the wheel has no button
#: of its own for. A CAM tap with no second button is the camera key, "c".
LAYER_BUTTON = 21
LAYER_KEYS = {0: "r",        # reset car
              1: "m",        # mute
              2: "h",        # hide HUD
              3: "t",        # driver aids on/off
              4: "o",        # settings (menus)
              5: "g",        # watch next car
              6: "f",        # free camera
              7: "p",        # hide pause card
              12: "q"}       # back to the racing line (auto steering)

# Gamepad (Switch Pro Controller as Windows reports it): B A X Y = 0..3,
# L R ZL ZR = 4..7, - + = 8, 9, stick clicks 10, 11, home 12, capture 13; the
# D-pad is the POV hat, the sticks are X Y (left) and U R (right).
PAD_KEYS = {1: "enter",          # A: confirm; in a race: look back
            2: "c",              # X: camera
            3: "tab",            # Y: interval / leader
            4: "gear down", 5: "gear up",      # L / R: shift (manual gearbox)
            9: "escape",         # +: pause
            10: "m",             # left stick click: mute
            11: "h",             # right stick click: hide HUD
            12: "o"}             # home: settings (menus)
#: Hold - and press: the keys left over. A tap of - on its own is "t" (aids).
PAD_LAYER = 8
PAD_LAYER_KEYS = {3: "r",        # Y: reset car
                  1: "g",        # A: watch next car
                  2: "f",        # X: free camera
                  0: "q",        # B: back to the racing line
                  4: "p", 5: "x"}  # L: hide pause card, R: look back
PAD_POV = {0: "up arrow", 90: "right arrow", 180: "down arrow", 270: "left arrow"}

_shared: Wheel | None = None


def shared() -> Wheel:
    global _shared
    if _shared is None:
        _shared = Wheel()
    return _shared


def _dir_keys(w: Wheel, racing: bool) -> list[str]:
    """Arrow keys from the D-pad (and the left stick outside a race), with
    auto-repeat while held, for the menus."""
    d = PAD_POV.get(w.pov)
    if d is None and not racing:
        x, y = w.stick("X"), w.stick("Y")
        if max(abs(x), abs(y)) > 0.6:
            d = (("right arrow" if x > 0 else "left arrow") if abs(x) > abs(y)
                 else ("down arrow" if y > 0 else "up arrow"))
    now = time.monotonic()
    if d is None:
        w._dir = None
        return []
    if d != getattr(w, "_dir", None):
        w._dir, w._dir_t = d, now + 0.40
        return [d]
    if now >= w._dir_t:
        w._dir_t = now + 0.12
        return [d]
    return []


def poll_keys(menu: bool = False, racing: bool = False,
              ingame: bool = False) -> list[str]:
    """Once per frame: read the device and return the keys its buttons just pressed."""
    w = shared()
    prev = getattr(w, "_prev_buttons", 0)
    if not w.read():
        w._prev_buttons = 0
        return []
    w._prev_buttons = w.buttons
    new = w.buttons & ~prev
    pad = w.kind == "gamepad"
    lay_btn = PAD_LAYER if pad else LAYER_BUTTON
    lay_keys = PAD_LAYER_KEYS if pad else LAYER_KEYS
    tap = "t" if pad else "c"
    keymap = PAD_KEYS if pad else KEY_BUTTONS
    back = 0 if pad else 1                   # B: handbrake in a race, "back" in menus
    keys = []
    if w.buttons >> lay_btn & 1:
        keys += [k for i, k in lay_keys.items() if new >> i & 1]
        if new & ~(1 << lay_btn):
            w._layer_used = True
        if new >> lay_btn & 1:
            w._layer_used = bool(new & ~(1 << lay_btn))
        return keys
    if prev >> lay_btn & 1 and not getattr(w, "_layer_used", False):
        keys.append(tap)
    w._layer_used = False
    keys += [k for i, k in keymap.items() if new >> i & 1]
    if pad:
        keys += _dir_keys(w, racing)
    if ingame and not pad:           # on the wheel X is ESC in a session (+ is, on a pad)
        keys = ["escape" if k == "x" else k for k in keys]
    if racing:                       # driving: A looks back instead of ENTER, Y is DRS
        keys = ["x" if k == "enter" else "v" if k == "tab" else k for k in keys]
    if (menu or (ingame and not racing)) and new >> back & 1:
        keys.append("escape")
    return keys
