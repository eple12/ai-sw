"""The optimal racing line as a hologram on the road, with braking hints.

Drawn from the best solved lap of the circuit (the highest-grip plan of the
min-time solution, tools/mintime.py): a translucent ribbon along the line,
coloured by what the solution does with the car there --

* green   full power / cruising,
* yellow  lifting or trail-braking lightly,
* red     braking, deeper red the harder,

plus a bar across the road at the start of every braking zone, whose width and
brightness grow with the speed that is about to be shed (the braking point).
Toggle: settings.racing_line (live, also from the pause card).
"""
from __future__ import annotations

import math

import numpy as np
from panda3d.core import TransparencyAttrib
from ursina import Entity, Mesh, scene

from . import config, teams
from .mintime_driver import Plan, path_file

WIDTH = 1.0              # ribbon width, m
LIFT = 0.012             # above the painted line (config.Y_LINE), m
ALPHA = 0.62
BRAKE_A = 3.0            # m/s^2 of deceleration where red starts
HARD_A = 14.0            # ... and where it is fully red
BAR_MIN_DROP = 6.0       # m/s a zone must shed to get a marker bar

GREEN = np.array([0.18, 0.95, 0.35])
YELLOW = np.array([1.00, 0.85, 0.15])
RED = np.array([1.00, 0.10, 0.08])


def best_plan_path(circuit: str):
    for g in teams.PLAN_GRIPS:                       # highest grip first
        p = path_file(circuit, f"g{round(g * 100):02d}")
        if p.exists():
            return p
    return None


def available(circuit: str) -> bool:
    return best_plan_path(circuit) is not None


def _decel(v: np.ndarray, seg: np.ndarray) -> np.ndarray:
    """Longitudinal acceleration along the line, m/s^2 (negative = braking)."""
    vn = np.roll(v, -1)
    a = (vn * vn - v * v) / (2.0 * np.maximum(seg, 1e-6))
    k = np.array([0.25, 0.5, 0.25])
    return np.convolve(np.r_[a[-1], a, a[0]], k, mode="valid")


def _colour(a: float) -> np.ndarray:
    if a < -BRAKE_A:
        t = min(1.0, (-a - BRAKE_A) / (HARD_A - BRAKE_A))
        return YELLOW + (RED - YELLOW) * (0.45 + 0.55 * t)
    if a < 0.0:                                      # coasting / light lift
        return GREEN + (YELLOW - GREEN) * (-a / BRAKE_A)
    return GREEN


class RacingLine:
    def __init__(self, track):
        path = best_plan_path(track.name)
        self.entity = None
        if path is None:
            return
        plan = Plan(path)
        n = plan.n
        a = _decel(plan.v, plan.seg)
        verts, cols, tris = [], [], []
        half = WIDTH / 2.0
        side = plan.nrm
        # the plan's "right" must be the track's right
        if float(np.sum(side[0] * track.normal[0])) < 0:
            side = -side
        for k in range(n):
            x, z = plan.xy[k]
            nx, nz = side[k]
            y = float(track.surface_y(k, float(plan.n_raw[k]))) + config.Y_LINE + LIFT
            c = _colour(float(a[k]))
            for sgn in (-1.0, 1.0):
                verts.append((x + sgn * nx * half, y, z + sgn * nz * half))
                cols.append((c[0], c[1], c[2], ALPHA))
        for k in range(n):
            j = (k + 1) % n
            q = 2 * k
            r = 2 * j
            tris += [q, q + 1, r, q + 1, r + 1, r]

        # braking-point bars across the road
        braking = a < -BRAKE_A
        for k in range(n):
            if braking[k] and not braking[k - 1]:
                m = k
                while m < n + k and braking[m % n]:
                    m += 1
                drop = float(plan.v[k] - plan.v[m % n])
                if drop < BAR_MIN_DROP:
                    continue
                t = min(1.0, drop / 40.0)
                w = 2.2 + 3.0 * t                    # half-width across, m
                c = RED + (np.array([1.0, 1.0, 1.0]) - RED) * 0.35
                x, z = plan.xy[k]
                nx, nz = side[k]
                tx, tz = plan.tan[k]
                y = float(track.surface_y(k, float(plan.n_raw[k]))) + config.Y_LINE + LIFT + 0.004
                d = 0.9                                # bar thickness along the road
                base = len(verts)
                for sx, sz in ((-w, 0.0), (w, 0.0), (w, d), (-w, d)):
                    verts.append((x + nx * sx + tx * sz, y, z + nz * sx + tz * sz))
                    cols.append((c[0], c[1], c[2], 0.5 + 0.4 * t))
                tris += [base, base + 1, base + 2, base, base + 2, base + 3]

        e = Entity(parent=scene, unlit=True,
                   model=Mesh(vertices=verts, triangles=tris, colors=cols,
                              mode="triangle", static=True))
        e.setTransparency(TransparencyAttrib.M_alpha)
        e.set_depth_write(False)
        e.set_bin("fixed", 20)
        self.entity = e
        self.lap_time = plan.lap_time

    @property
    def ok(self) -> bool:
        return self.entity is not None

    def show(self, on: bool) -> None:
        if self.entity is not None and self.entity.enabled != on:
            self.entity.enabled = on

    def destroy(self) -> None:
        if self.entity is not None:
            from ursina import destroy
            destroy(self.entity)
            self.entity = None
