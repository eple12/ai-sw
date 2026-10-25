"""Shoot scripted footage of the game for the promo videos (promo/).

    python tools/shoot.py --list
    python tools/shoot.py drive --out promo/footage            # -> drive.mp4 + drive.json
    python tools/shoot.py drive --size 1280 720 --max 4 --shots 1 3

The game runs offscreen with the clock in non-real-time mode: every frame is
exactly 1/fps of game time whatever the GPU takes to draw it, so a clip is
smooth and a take can be repeated. Each frame is read back and piped to ffmpeg.

The wheel is a *virtual* MOZA: the scene scripts the steering, the pedals and
the buttons, and the game's own ``wheel.poll_keys`` turns the button bits into
keys, so the mapping on screen is the mapping the real wheel gets. Next to the
video goes ``<scene>.json``: per frame the controls the car actually received
(steer, throttle, brake), speed, gear, the buttons held, so the overlay in the
video can turn a drawn wheel and press drawn pedals in step with the picture.

The user's settings file is never written (``settings.save`` is a no-op here).
"""
from __future__ import annotations

import os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Wheel button indices as ``game/wheel.py`` reads them (MOZA ES wheel).
BTN = {"A": 0, "B": 1, "X": 2, "Y": 3, "UP": 4, "RIGHT": 5, "DOWN": 6, "LEFT": 7,
       "DOWNSHIFT": 12, "UPSHIFT": 13, "CAM": 21, "HOME": 24}


def bits(*names: str) -> int:
    return sum(1 << BTN[n] for n in names)


class Ctx:
    """What a scene sees and drives. A scene is a generator: it sets things
    here, then ``yield``s once per frame (``yield from ctx.wait(s)``)."""

    def __init__(self, fps: int):
        self.fps = fps
        self.t = 0.0                  # seconds since recording started
        self.frame = 0                # frames recorded
        self.recording = False
        self.steer = self.throttle = self.brake = 0.0
        self.buttons = 0
        self.log: list[dict] = []
        self.notes: dict = {}
        self.shots: dict[float, str] = {}

    # -- timing -----------------------------------------------------------
    def wait(self, seconds: float):
        for _ in range(max(1, round(seconds * self.fps))):
            yield

    def tap(self, *names: str, hold: float = 0.12):
        """Press the buttons together, hold, release."""
        self.buttons |= bits(*names)
        yield from self.wait(hold)
        self.buttons &= ~bits(*names)
        yield from self.wait(0.05)

    def hold(self, *names: str):
        self.buttons |= bits(*names)

    def release(self, *names: str):
        self.buttons &= ~bits(*names)

    def ramp(self, attr: str, to: float, seconds: float):
        """Move steer / throttle / brake to a value over some seconds."""
        n = max(1, round(seconds * self.fps))
        a = getattr(self, attr)
        for k in range(1, n + 1):
            setattr(self, attr, a + (to - a) * k / n)
            yield

    def mark(self, name: str):
        self.notes[name] = round(self.t, 3)


def build_virtual_wheel(ctx: Ctx):
    from game import wheel as wm

    class VWheel(wm.Wheel):
        def read(self):
            self.connected = True
            self.buttons = ctx.buttons
            self.pov = -1
            return True

        @property
        def steer(self):
            return ctx.steer

        @property
        def throttle(self):
            return ctx.throttle

        @property
        def brake(self):
            return ctx.brake

    w = VWheel()
    w.connected = True
    w.kind = "wheel"
    wm._shared = w
    return w


def snap_extra(g) -> dict:
    """The player's tow (drag factor), DRS state and gap, from the field's snapshot (grand prix)."""
    try:
        row = g._snap["rows"][g.field.player]
        C = g._C
        return dict(drag=round(float(row[C["drag"]]), 3), drs=int(row[C["drs"]]),
                    drs_ok=int(row[C["drs_ok"]]))
    except Exception:
        return {}


# --- scenes ------------------------------------------------------------------
#: name -> dict(track, mode, level, settings, fn). ``fn(ctx, game_mod)``.
SCENES: dict = {}


def scene(name, **meta):
    def deco(fn):
        SCENES[name] = dict(meta, fn=fn)
        return fn
    return deco


def place_at(ga, s_m: float, kmh: float):
    """Put the player's car on the centreline *s_m* metres past the line, going
    *kmh* along the track, with the race running."""
    import numpy as np
    g = ga.GAME
    tr = g.track
    i = int(np.searchsorted(tr.arclen, s_m % tr.length)) % tr.count
    tan = tr.tangent[i]
    g.vehicle.place(tr.center[i].copy(), math.atan2(tan[0], tan[1]))
    g.vehicle.vel[:] = tan * (kmh / 3.6)
    g.surface.hint = i
    g.vehicle.frozen = False
    g._snap_camera()
    return i


#: Every scene starts from these, whatever the user's own settings file says.
BASE = dict(auto_steer=False, auto_pedals=False, drs_manual=False, manual_gearbox=False,
            racing_line=False, steer_assist=False, traction_control=True, abs_enabled=True,
            skid_marks=True, drs=True, slipstream=True, steer_sens=1.0, edge_guard=False)
AUTO = dict(auto_steer=True, auto_pedals=True)


def line_back(ga, m: float) -> float:
    """A distance *m* metres before the start line, as a lap distance."""
    return ga.GAME.track.length - m


@scene("lap_t1", track="Monza", mode="quali", level=6, settings=AUTO)
def sc_lap_t1(ctx, ga):
    """Down the main straight, braking for the first chicane: the pedals and
    the wheel at work. The car drives itself; the log keeps what it did."""
    place_at(ga, line_back(ga, 760), 110)
    ctx.recording = True
    yield from ctx.wait(26)


@scene("steer_para", track="Monza", mode="quali", level=6, settings=AUTO)
def sc_steer_para(ctx, ga):
    """Round the long right-hander before the main straight: the wheel."""
    place_at(ga, line_back(ga, 1150), 150)
    ctx.recording = True
    yield from ctx.wait(14)


@scene("pedals_t1", track="Monza", mode="quali", level=6, settings=AUTO)
def sc_pedals_t1(ctx, ga):
    """From the line: flat out, then the brakes for the first chicane."""
    place_at(ga, 5, 120)
    ctx.recording = True
    yield from ctx.wait(22)


@scene("cams", track="Monza", mode="quali", level=6, settings=AUTO)
def sc_cams(ctx, ga):
    """CAM, three times, a long look at each view: far, onboard, back to the chase camera."""
    place_at(ga, line_back(ga, 1150), 150)
    ctx.recording = True
    yield from ctx.wait(1.5)
    for _ in range(3):
        ctx.mark("cam")
        yield from ctx.tap("CAM")
        yield from ctx.wait(3.3)


@scene("lookback", track="Monza", mode="quali", level=6, settings=AUTO)
def sc_lookback(ctx, ga):
    """A: look back; A again: forwards."""
    place_at(ga, line_back(ga, 900), 190)
    ctx.recording = True
    yield from ctx.wait(1.5)
    ctx.mark("back")
    yield from ctx.tap("A")
    yield from ctx.wait(3.8)
    ctx.mark("front")
    yield from ctx.tap("A")
    yield from ctx.wait(2.6)


@scene("reset", track="Monza", mode="quali", level=6)
def sc_reset(ctx, ga):
    """Off the road, then CAM + A: the car is put back on the track."""
    place_at(ga, 5, 150)
    ctx.recording = True
    ctx.throttle = 1.0
    yield from ctx.wait(1.0)
    yield from ctx.ramp("steer", 0.16, 0.5)         # drift right, over the kerb, onto the grass
    yield from ctx.wait(2.0)
    ctx.steer, ctx.throttle = 0.0, 0.0
    yield from ctx.ramp("brake", 1.0, 0.3)
    yield from ctx.wait(2.6)
    ctx.mark("chord")
    ctx.hold("CAM")
    yield from ctx.wait(0.5)
    yield from ctx.tap("A", hold=0.2)
    ctx.release("CAM")
    ctx.brake = 0.0
    yield from ctx.ramp("throttle", 0.7, 0.4)
    yield from ctx.wait(3.2)


@scene("pause", track="Monza", mode="quali", level=6, settings=AUTO)
def sc_pause(ctx, ga):
    """X: pause card. The D-pad moves on it, X goes back to the race."""
    place_at(ga, line_back(ga, 900), 190)
    ctx.recording = True
    yield from ctx.wait(1.5)
    ctx.mark("pause")
    yield from ctx.tap("X")
    yield from ctx.wait(2.0)
    for name in ("DOWN", "DOWN", "UP", "UP"):
        yield from ctx.tap(name)
        yield from ctx.wait(1.4)
    ctx.mark("resume")
    yield from ctx.tap("X")
    yield from ctx.wait(2.6)


@scene("steer_run", track="Monza", mode="quali", level=6, settings=AUTO)
def sc_steer_run(ctx, ga):
    """Flat out from the line through the first chicane, the sweeper and the second
    chicane: one long watch of the wheel turning both ways."""
    place_at(ga, 5, 270)
    ctx.recording = True
    yield from ctx.wait(34)


@scene("shift", track="Monza", mode="quali", level=6,
       settings=dict(auto_steer=True, manual_gearbox=True))
def sc_shift(ctx, ga):
    """Manual gearbox: the right paddle up the gears, the left one down."""
    place_at(ga, 5, 25)
    ctx.recording = True
    ctx.throttle = 1.0
    yield from ctx.wait(0.4)
    v = ga.GAME.vehicle
    ups = 0
    for _ in range(round(14 * ctx.fps)):
        if ups < 5 and v.revs_in(v.gear, v.speed) > 0.93:
            ctx.mark(f"up{ups + 1}")
            yield from ctx.tap("UPSHIFT")
            ups += 1
            yield from ctx.wait(0.35)
        else:
            yield
        if ups >= 5 and v.speed * 3.6 > 205:
            break
    ctx.throttle = 0.0
    yield from ctx.ramp("brake", 0.9, 0.25)
    for k in range(2):
        yield from ctx.wait(0.55)
        ctx.mark(f"down{k + 1}")
        yield from ctx.tap("DOWNSHIFT")
    yield from ctx.wait(1.4)


@scene("menu", menu=True, mode=None)
def sc_menu(ctx, ga):
    """Main menu and circuit list with the D-pad and A, one press every ~1.8 s."""
    ctx.recording = True
    yield from ctx.wait(0.8)
    for name in ("RIGHT", "LEFT", "DOWN", "RIGHT", "UP"):
        yield from ctx.tap(name)
        yield from ctx.wait(1.6)
    yield from ctx.tap("A")
    yield from ctx.wait(2.2)
    for name in ("DOWN", "DOWN", "UP"):
        yield from ctx.tap(name)
        yield from ctx.wait(1.6)
    yield from ctx.wait(0.8)


@scene("gp_probe", track="Monza", mode="gp", level=2, settings=AUTO)
def sc_gp_probe(ctx, ga):
    """A grand prix from the grid, to see that the field runs under the fixed clock."""
    ctx.recording = True
    yield from ctx.wait(26)


# --- the rules video ---------------------------------------------------------
def lane(ga):
    """(lateral offset in m, + = right of the centreline; the white line on that side)."""
    g = ga.GAME
    v = g.vehicle
    _, off, edge = g.surface._local((float(v.pos[0]), float(v.pos[1])))
    return off, edge


def follow(ctx, ga, rel_fn, seconds, kp=0.07, kd=0.05, throttle=None):
    """Steer to hold a lateral position for *seconds*. ``rel_fn(t)`` gives it in metres
    PAST the right-hand white line (negative = inside the road; -edge = the centreline)."""
    prev = None
    for k in range(round(seconds * ctx.fps)):
        off, edge = lane(ga)
        tgt = edge + rel_fn(k / ctx.fps)
        lv = 0.0 if prev is None else (off - prev) * ctx.fps
        prev = off
        ctx.steer = max(-1.0, min(1.0, kp * (tgt - off) - kd * lv))
        if throttle is not None:
            ctx.throttle = throttle
        yield


def rc_log(ctx, ga, since):
    """Print the stewards' messages that appeared since index *since*; return the new index."""
    f = ga.GAME.field
    if f is None:
        return since
    for m in f.msgs[since:]:
        print("RC", round(ctx.t, 2), m)
    return len(f.msgs)


def wait_for(ctx, ga, cond, limit=40.0):
    for _ in range(round(limit * ctx.fps)):
        if cond(ga.GAME):
            return
        yield


@scene("q_flow", track="Monza", mode="quali", level=2, laps=3)
def sc_q_flow(ctx, ga):
    """Qualifying: the out lap, then the line, then lap 1 of the timed laps."""
    g = ga.GAME
    place_at(ga, line_back(ga, 380), 110)
    g.armed = True
    ctx.recording = True
    ctx.throttle = 1.0
    yield from ctx.wait(1.0)
    yield from follow(ctx, ga, lambda t: -lane(ga)[1], 13.0, throttle=0.8)


@scene("q_invalid", track="Monza", mode="quali", level=2, laps=3)
def sc_q_invalid(ctx, ga):
    """A timed lap: two wheels over the white line are fine, all four are not."""
    g = ga.GAME
    place_at(ga, 20, 130)
    g.lap_num, g.lap_invalid = 1, False
    g.lap_start = g.session_time
    ctx.recording = True
    ctx.throttle = 0.55
    yield from follow(ctx, ga, lambda t: -2.0, 2.5)            # on the road, a wheel near the line
    ctx.mark("two_wheels")
    yield from follow(ctx, ga, lambda t: -0.3, 3.2)            # two wheels over: still on
    ctx.mark("all_four")
    yield from follow(ctx, ga, lambda t: 2.6, 3.0)             # all four over: the lap is gone
    ctx.mark("back")
    yield from follow(ctx, ga, lambda t: -2.5, 4.0)
    ctx.brake = 0.0


@scene("q_finish", track="Monza", mode="quali", level=2, laps=3)
def sc_q_finish(ctx, ga):
    """The last timed lap ends: chequered flag and the result."""
    g = ga.GAME
    place_at(ga, line_back(ga, 400), 150)
    g.armed = True
    g.lap_num = g.total_laps
    g.lap_start = g.session_time - 91.0
    g.best_t = 92.5
    ctx.recording = True
    ctx.throttle = 0.8
    yield from follow(ctx, ga, lambda t: -lane(ga)[1], 12.0, throttle=0.8)


@scene("gp_start", track="Monza", mode="gp", level=2, grid=1, laps=3)
def sc_gp_start(ctx, ga):
    """From the grid: gantry, five lights, lights out, the launch."""
    g = ga.GAME
    ctx.recording = True
    yield from wait_for(ctx, ga, lambda g: g.state == 1)       # RACING: the lights went out
    yield from ctx.wait(0.28)
    ctx.throttle = 1.0
    yield from follow(ctx, ga, lambda t: -lane(ga)[1], 7.0, throttle=1.0)


@scene("gp_jump", track="Monza", mode="gp", level=2, grid=1, laps=3)
def sc_gp_jump(ctx, ga):
    """Throttle with all five lights on: the stewards see it."""
    g = ga.GAME
    ctx.recording = True
    yield from wait_for(ctx, ga, lambda g: g.start_t >= g._T_build1 + 0.9)
    ctx.mark("throttle")
    ctx.throttle = 1.0
    seen = 0
    yield from wait_for(ctx, ga, lambda g: g.state == 1)
    yield from follow(ctx, ga, lambda t: -lane(ga)[1], 8.0, throttle=1.0)


@scene("gp_limits", track="Monza", mode="gp", level=2, grid=1, laps=3)
def sc_gp_limits(ctx, ga):
    """Running wide again and again: warnings 1-4, the black and white flag, then seconds."""
    g = ga.GAME
    ctx.recording = True
    yield from wait_for(ctx, ga, lambda g: g.state == 1)
    yield from ctx.wait(0.28)
    ctx.throttle = 1.0
    seen = 0
    yield from follow(ctx, ga, lambda t: -lane(ga)[1], 2.2, throttle=1.0)
    for k in range(9):
        ctx.mark(f"wide{k + 1}")
        yield from follow(ctx, ga, lambda t: 2.6, 0.95, throttle=1.0)
        yield from follow(ctx, ga, lambda t: -lane(ga)[1] + 1.2, 1.75, throttle=1.0)
        seen = rc_log(ctx, ga, seen)
    yield from follow(ctx, ga, lambda t: -lane(ga)[1], 1.5, throttle=1.0)
    seen = rc_log(ctx, ga, seen)
    ctx.throttle, ctx.brake = 0.0, 1.0                     # let the last messages take their turn
    ctx.steer = 0.0
    yield from ctx.wait(9.0)
    seen = rc_log(ctx, ga, seen)


@scene("gp_yellow", track="Monza", mode="gp", level=2, grid=1, laps=3)
def sc_gp_yellow(ctx, ga):
    """The player's car stops on the grass: a yellow flag for everyone behind."""
    g = ga.GAME
    ctx.recording = True
    yield from wait_for(ctx, ga, lambda g: g.state == 1)
    yield from ctx.wait(0.28)
    ctx.throttle = 1.0
    seen = 0
    yield from follow(ctx, ga, lambda t: -lane(ga)[1], 6.0, throttle=1.0)
    ctx.mark("pull_off")
    ctx.throttle = 0.0
    yield from follow(ctx, ga, lambda t: -1.5, 3.5)            # to the edge, still inside the line
    ctx.brake = 1.0
    ctx.steer = 0.0
    for _ in range(round(14 * ctx.fps)):
        seen = rc_log(ctx, ga, seen) if (ctx.frame % 15 == 0) else seen
        yield


@scene("rline", track="Monza", mode="quali", level=2, laps=3,
       settings=dict(auto_steer=True, auto_pedals=True, racing_line=True))
def sc_rline(ctx, ga):
    """The racing-line hologram: green on the power, yellow where to lift, red where to brake."""
    place_at(ga, 5, 150)
    ctx.recording = True
    yield from ctx.wait(22)


@scene("settings_scroll", menu=True, mode=None,
       settings=dict(auto_steer=False, auto_pedals=False, racing_line=True, manual_gearbox=False,
                     edge_guard=True, drs=True, drs_manual=True, pen_track_limits=False,
                     pen_collision=False, pen_yellow=False, jump_start=False, traction_control=True,
                     abs_enabled=True, steer_assist=True, skid_marks=True, slipstream=True,
                     yellow_flags=True, steer_sens=1.0))
def sc_settings_scroll(ctx, ga):
    """The settings screen, one row at a time (18 rows), with the beginner values the video recommends."""
    ctx.recording = True
    yield from ctx.wait(1.0)
    ga.input("o")                                              # SETTINGS
    yield from ctx.wait(2.0)
    for k in range(17):
        ctx.mark(f"row{k + 1}")
        ga.input("down arrow")
        yield from ctx.wait(3.0)
    yield from ctx.wait(1.0)


@scene("diff_menu", menu=True, mode=None)
def sc_diff_menu(ctx, ga):
    """The difficulty row of the main menu, stepped from 2 up to 6 and down to 1."""
    ctx.recording = True
    yield from ctx.wait(1.5)
    yield from ctx.tap("DOWN")
    yield from ctx.wait(1.8)
    for name in ("LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT", "LEFT", "LEFT", "LEFT", "LEFT"):
        ctx.mark(name)
        yield from ctx.tap(name)
        yield from ctx.wait(1.8)
    yield from ctx.wait(1.0)


@scene("grid_menu", menu=True, mode="gp", rerender=True)
def sc_grid_menu(ctx, ga):
    """Grand prix circuit list: A / D picks the grid slot (QUALI, then 1 to 20)."""
    ctx.recording = True
    yield from ctx.wait(2.0)
    for name in ("RIGHT", "RIGHT", "RIGHT", "LEFT", "LEFT", "LEFT"):
        ctx.mark(name)
        yield from ctx.tap(name)
        yield from ctx.wait(1.9)
    yield from ctx.wait(1.0)


# --- free-camera shots --------------------------------------------------------
def free_cam(ga, on=True, drive=None):
    """Switch the free camera on (the car then drives from *drive*, a function returning
    Controls, because the game hands a free-camera session zero controls)."""
    from game.vehicle import Controls
    g = ga.GAME
    if on and not g.freecam.enabled:
        g.on_key("f")
    if drive is not None:
        g.read_controls = drive
    return Controls


def aim(ga, pos, target):
    """Put the free camera at *pos* (x, y, z) looking at *target* (x, y, z)."""
    import math as m
    fc = ga.GAME.freecam
    dx, dy, dz = target[0] - pos[0], target[1] - pos[1], target[2] - pos[2]
    fc.pos = type(fc.pos)(*pos)
    fc.yaw = m.degrees(m.atan2(dx, dz))
    fc.pitch = m.degrees(m.atan2(-dy, m.hypot(dx, dz)))


def car_frame(ga):
    """Position, forward and right vectors of the player's car on the ground plane, and its height."""
    import math as m
    v = ga.GAME.vehicle
    sy, cy = m.sin(v.yaw), m.cos(v.yaw)
    return (float(v.pos[0]), float(v.pos[1])), (sy, cy), (cy, -sy), float(getattr(v, "surface_y", 0.0))


@scene("tl_wheels", track="Monza", mode="quali", level=2, laps=3)
def sc_tl_wheels(ctx, ga):
    """The car drifts over the right-hand white line: the camera rides beside it, outside the line,
    so the wheels and the line are in the picture together."""
    from game.vehicle import Controls
    g = ga.GAME
    place_at(ga, 420, 62)
    g.lap_num, g.lap_invalid = 1, False
    g.lap_start = g.session_time
    g.armed = False
    ctx.recording = True
    drive = lambda: Controls(throttle=ctx.throttle, brake=ctx.brake, steer=ctx.steer, analog_steer=True)
    free_cam(ga, True, drive)
    ctx.throttle = 0.28
    t = 0.0
    N = round(15 * ctx.fps)
    prev = None
    for k in range(N):
        t = k / ctx.fps
        off, edge = lane(ga)
        # inside the line, then over it with two wheels, then all four, then back
        if t < 3.0:
            rel = -2.6
        elif t < 8.0:
            rel = -2.6 + (t - 3.0) / 5.0 * 4.0          # to 1.4 m past the line
        elif t < 10.5:
            rel = 1.4
        else:
            rel = 1.4 - (t - 10.5) / 4.5 * 4.2
        tgt = edge + rel
        lv = 0.0 if prev is None else (off - prev) * ctx.fps
        prev = off
        ctx.steer = max(-1.0, min(1.0, 0.08 * (tgt - off) - 0.06 * lv))
        (px, pz), f, r, y = car_frame(ga)
        # beside the car, outside the line, a little ahead, low
        cx = px + r[0] * 8.5 + f[0] * 1.0
        cz = pz + r[1] * 8.5 + f[1] * 1.0
        aim(ga, (cx, y + 3.6, cz), (px + r[0] * 0.2, y + 0.0, pz + r[1] * 0.2))
        ctx.throttle = 0.3 if ga.GAME.vehicle.speed < 17.0 else 0.0
        yield
    ctx.throttle = 0.0
    ctx.brake = 0.6


@scene("lights_cam", track="Monza", mode="gp", level=2, grid=1, laps=3)
def sc_lights_cam(ctx, ga):
    """The start gantry from behind the pole car, the five lights coming on."""
    g = ga.GAME
    ctx.recording = True
    yield from ctx.wait(0.5)
    free_cam(ga, True)
    for k in range(round(10 * ctx.fps)):
        (px, pz), f, r, y = car_frame(ga)
        # behind and above the pole car, looking up the grid at the gantry
        aim(ga, (px - f[0] * 14 + r[0] * 3.2, y + 2.6, pz - f[1] * 14 + r[1] * 3.2),
            (px + f[0] * 12, y + 5.2, pz + f[1] * 12))
        yield


@scene("slip_gp", track="Monza", mode="gp", level=4, grid=14, laps=3, settings=AUTO)
def sc_slip_gp(ctx, ga):
    """From the back of the grid with the assist driving: the tow behind the cars ahead."""
    ctx.recording = True
    yield from ctx.wait(70)


@scene("wall", track="Monza", mode="quali", level=2, laps=3, settings=dict(edge_guard=True))
def sc_wall(ctx, ga):
    """The track-limit wall at work: the car is steered out over the white line and cannot go;
    it is held on the line and slides along it. The camera rides beside it, outside the line."""
    from game.vehicle import Controls
    g = ga.GAME
    place_at(ga, 420, 62)
    g.lap_num, g.lap_invalid = 1, False
    g.lap_start = g.session_time
    g.armed = False
    ctx.recording = True
    drive = lambda: Controls(throttle=ctx.throttle, brake=ctx.brake, steer=ctx.steer, analog_steer=True)
    free_cam(ga, True, drive)
    ctx.throttle = 0.3
    prev = None
    for k in range(round(15 * ctx.fps)):
        t = k / ctx.fps
        off, edge = lane(ga)
        if t < 3.0:
            rel = -2.6
        elif t < 6.0:
            rel = -2.6 + (t - 3.0) / 3.0 * 5.2        # steer out, well past the line
        else:
            rel = 2.6
        tgt = edge + rel
        lv = 0.0 if prev is None else (off - prev) * ctx.fps
        prev = off
        ctx.steer = max(-1.0, min(1.0, 0.08 * (tgt - off) - 0.06 * lv))
        (px, pz), f, r, y = car_frame(ga)
        cx = px + r[0] * 8.5 + f[0] * 1.0
        cz = pz + r[1] * 8.5 + f[1] * 1.0
        aim(ga, (cx, y + 3.6, cz), (px + r[0] * 0.2, y + 0.0, pz + r[1] * 0.2))
        ctx.throttle = 0.7 if ga.GAME.vehicle.speed < 27.0 else 0.0
        yield


@scene("collide", track="Monza", mode="gp", level=2, grid=20, laps=3)
def sc_collide(ctx, ga):
    """From the back of the grid, flat out with no brakes into the pack: a collision the stewards judge."""
    g = ga.GAME
    ctx.recording = True
    yield from wait_for(ctx, ga, lambda g: g.state == 1)
    yield from ctx.wait(0.25)
    ctx.throttle = 1.0
    seen = 0
    for k in range(round(48 * ctx.fps)):
        off, edge = lane(ga)
        ctx.steer = max(-1.0, min(1.0, 0.07 * (-edge * 0.3 - off)))
        if k % 15 == 0:
            seen = rc_log(ctx, ga, seen)
        yield


# --- runner ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out", default="promo/footage")
    ap.add_argument("--size", type=int, nargs=2, default=(1920, 1080))
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--max", type=float, default=0.0, help="stop after N seconds of footage")
    ap.add_argument("--shots", type=float, nargs="*", default=[],
                    help="also save PNGs at these seconds (into <out>/shots)")
    ap.add_argument("--crf", type=int, default=17)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--track", default=None)
    args = ap.parse_args()
    if args.list or not args.scene:
        print("\n".join(SCENES))
        return
    meta = SCENES[args.scene]
    track = args.track or meta.get("track", "Monza")

    from game import config, settings
    config.RENDER_THREADING = ""            # one thread: the read-back is then of this frame
    settings.load()
    for k, v in {**BASE, **meta.get("settings", {})}.items():
        setattr(settings.current, k, v)
    settings.save = lambda *a, **k: None    # never touch the user's file

    from ursina import Ursina, Text, application
    from panda3d.core import ClockObject
    from game import app as ga
    from game import frameloop

    frameloop.prepare()
    app = Ursina(title="FORMULA-AI", size=tuple(args.size), window_type="offscreen",
                 vsync=False, development_mode=False)
    frameloop.install(app)
    # An offscreen buffer never gets a window event, so Ursina's UI lens keeps
    # its 1 x 1 film and the whole HUD is drawn at the wrong scale.
    from ursina import camera, window
    camera.ui_lens.set_film_size(camera._ui_size * .5 * window.aspect_ratio,
                                 camera._ui_size * .5)
    from game.menu import pick_font
    font = pick_font()
    if font:
        Text.default_font = font
    import __main__
    import builtins

    clock = ClockObject.get_global_clock()
    clock.set_mode(ClockObject.M_non_real_time)
    clock.set_dt(1.0 / args.fps)

    ctx = Ctx(args.fps)
    build_virtual_wheel(ctx)
    if meta.get("hide_assist_word", True):
        ga.Game._assist_word = staticmethod(lambda *a, **k: "")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "shots").mkdir(exist_ok=True)
    if "grid" in meta:
        ga.SESSION["grid"] = meta["grid"]
    ga.SESSION.update(laps=meta.get("laps", 3), mute=True, mode=meta.get("mode"),
                      track=track, level=meta.get("level", 6))
    if meta.get("menu"):
        ga._build_menu()
    else:
        ga._build_race(track, meta.get("laps", 3), True, mode=meta["mode"], intro=False)

    proc = None
    if not args.no_video:
        W, H = args.size
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(args.fps),
               "-i", "-", "-vf", "vflip", "-c:v", "libx264", "-preset", "medium",
               "-crf", str(args.crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
               str(out / f"{args.scene}.mp4")]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    gen = meta["fn"](ctx, ga)
    state = {"done": False, "warm": 3}
    wanted = sorted(args.shots)

    def my_update():
        if state["warm"] > 0:                # let the scene finish building
            state["warm"] -= 1
            ga.update()
            return
        if not state["done"]:
            try:
                next(gen)
            except StopIteration:
                state["done"] = True
        ga.update()
        if ctx.recording:
            g = ga.GAME
            c = getattr(g, "_ctl", None) if g is not None else None
            ctx.log.append(dict(
                t=round(ctx.t, 4),
                steer=round(float(c.steer), 4) if c else 0.0,
                thr=round(float(c.throttle), 4) if c else 0.0,
                brk=round(float(c.brake), 4) if c else 0.0,
                kmh=round(g.vehicle.speed * 3.6, 1) if g is not None else 0.0,
                gear=int(g.vehicle.gear) if g is not None else 0,
                btn=int(ctx.buttons),
                **snap_extra(g)))

    def capture(task):
        if ctx.recording:
            if meta.get("rerender"):
                # a menu that rebuilds its map texture every few frames can be caught half-uploaded:
                # draw it once more and wait for the GPU before reading it back
                base.graphicsEngine.renderFrame()  # noqa: F821
                base.graphicsEngine.syncFrame()  # noqa: F821
            tex = base.win.getScreenshot()  # noqa: F821
            data = tex.getRamImageAs("BGR")
            if proc is not None:
                proc.stdin.write(bytes(data))
            while wanted and ctx.t >= wanted[0]:
                from panda3d.core import Filename
                p = out / "shots" / f"{args.scene}_{wanted.pop(0):05.1f}.png"
                base.win.saveScreenshot(Filename.fromOsSpecific(str(p)))  # noqa: F821
                print("shot", p.name)
            ctx.frame += 1
            ctx.t = ctx.frame / args.fps
            if args.max and ctx.t >= args.max:
                state["done"] = True
        if state["done"]:
            finish()
        return task.cont

    def finish():
        if proc is not None:
            proc.stdin.close()
            proc.wait()
        (out / f"{args.scene}.json").write_text(json.dumps(
            dict(scene=args.scene, fps=args.fps, frames=ctx.frame, notes=ctx.notes,
                 log=ctx.log), separators=(",", ":")))
        print(f"{args.scene}: {ctx.frame} frames, {ctx.t:.1f} s -> {out}")
        application.quit()
        os._exit(0)

    __main__.update = my_update
    base.taskMgr.add(capture, "shoot-capture", sort=55)  # noqa: F821
    app.run()


if __name__ == "__main__":
    main()
