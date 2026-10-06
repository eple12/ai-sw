"""The player's driving assists: auto steering, auto pedals, lane choice.

For somebody new to racing. The follower that drives the AI (``PlanFollower``,
mintime_driver.py) keeps the player's car on the solved racing line, shifted
sideways by a lane the player chooses with A / D, and it can work the pedals as
well, at the pace of the line and behind any car ahead. With both on, the whole
of driving is picking a lane.

* **Auto steering** -- the follower's wheel angle replaces the player's. A and D
  then step the lane (a tap moves one lane, 1.75 m, left or right, up to 6 m off
  the line and never closer than a wheel to the white line); the car eases across
  rather than snapping, as the AI's lane changes do.
* **Auto pedals** -- the follower's throttle and brake replace the player's.
  The speed is the line's at a beginner's pace (``PACE``), capped to what a car
  ahead in the lane leaves room for.

Either can be on alone. Settings only: nothing here can be changed mid-race.
"""
from __future__ import annotations

import math

from . import config, settings
from .mintime_driver import Plan, PlanFollower, path_file
from .vehicle import Controls

#: Lane step (m), reach (m), and how close the car's centre may get to a white
#: line (a wheel's half track and a quarter metre, as the AI's rooms are).
LANE_STEP = 1.75
LANE_MAX = 6.0
EDGE_ROOM = config.WHEEL_HALF_TRACK + 0.25
#: The share of the line's speed the pedals drive at, for a first-time driver.
PACE = 0.88
#: The lane change's natural frequency (rad/s): a change takes about 1.6 s.
OMEGA = 2.6
#: Plans to try, safest first: a beginner is better on a line solved for less
#: grip than the limit.
TAGS = ("g90", "g86", "g94", "g82", "g97", "g78", "g74")


def plan_path(circuit: str):
    for tag in TAGS:
        p = path_file(circuit, tag)
        if p.exists():
            return p
    return None


def available(circuit: str) -> bool:
    return plan_path(circuit) is not None


class Assist:
    def __init__(self, track, surface):
        self.track = track
        self.surface = surface
        self.plan = Plan(plan_path(track.name))
        self.follow = PlanFollower(self.plan)
        self.follow.track = track
        self.steer = settings.current.auto_steer
        self.pedals = settings.current.auto_pedals
        self.target = 0.0          # the lane chosen (m right of the line)
        self.offset = 0.0          # where the car is steering for now
        self.rate = 0.0
        self._i = 0

    # -- lane ---------------------------------------------------------------
    def step_lane(self, direction: int) -> None:
        """One lane left (-1) or right (+1); the road's room is applied every
        tick, so asking for more than it holds just goes to the edge."""
        self.target = min(max(self.target + direction * LANE_STEP, -LANE_MAX), LANE_MAX)

    def center(self) -> None:
        self.target = 0.0

    def _room(self, k: int) -> tuple[float, float]:
        """(left limit, right limit) of the lane offset, now and a little way on."""
        pl, tr = self.plan, self.track
        lo, hi = -LANE_MAX, LANE_MAX
        for m in (0.0, 40.0, 90.0):
            kk, _ = pl.ahead(k, 0.0, m) if m else (k, 0.0)
            n = float(pl.n_raw[kk])
            lo = max(lo, -tr.w_left[kk] + EDGE_ROOM - n)
            hi = min(hi, tr.w_right[kk] - EDGE_ROOM - n)
        return min(lo, 0.0), max(hi, 0.0)

    # -- per tick -----------------------------------------------------------
    def controls(self, vehicle, manual: Controls, dt: float, cap: float = math.inf) -> Controls:
        i, _ = self.surface.progress(vehicle.pos)
        k, f = self.plan.locate(vehicle.pos, i)
        lo, hi = self._room(k)
        want = min(max(self.target, lo), hi)
        speed = max(vehicle.speed, 5.0)
        acc = OMEGA * OMEGA * (want - self.offset) - 2.0 * OMEGA * self.rate
        self.rate += acc * dt
        self.offset += self.rate * dt
        ctl = self.follow.controls(vehicle, k, f, offset=self.offset,
                                   d_off=self.rate / speed, dd_off=acc / (speed * speed),
                                   v_cap=cap, pace=PACE if self.pedals else 1.0)
        out = Controls(throttle=manual.throttle, brake=manual.brake, steer=manual.steer,
                       handbrake=manual.handbrake)
        if self.steer:
            out.steer = ctl.steer
            out.analog_steer = True
        if self.pedals:
            out.throttle, out.brake = ctl.throttle, ctl.brake
        return out

    @property
    def lane_text(self) -> str:
        n = round(self.target / LANE_STEP)
        side = "L" if n < 0 else "R" if n > 0 else "LINE"
        return side if n == 0 else f"{side} {abs(n)}"


def leader_cap(vehicle, others) -> float:
    """Speed (m/s) that keeps a gap to the nearest car ahead in the player's
    lane; ``inf`` if there is none. *others*: vehicles of the cars around."""
    sy, cy = math.sin(vehicle.yaw), math.cos(vehicle.yaw)
    px, pz = float(vehicle.pos[0]), float(vehicle.pos[1])
    best = None
    for o in others:
        dx, dz = float(o.pos[0]) - px, float(o.pos[1]) - pz
        ahead = dx * sy + dz * cy
        side = dx * cy - dz * sy
        if 2.0 < ahead < 80.0 and abs(side) < 2.8 and (best is None or ahead < best[0]):
            best = (ahead, float(o.vel[0]) * sy + float(o.vel[1]) * cy)
    if best is None:
        return math.inf
    gap, v = best
    return max(v, 0.0) * 0.97 + max(gap - 10.0, 0.0) * 0.45 if gap > 10.0 else max(v, 0.0) * 0.85
