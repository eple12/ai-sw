"""Tyre marks laid on the road by locked wheels and slides.

Per wheel, a strip is extended while the tyre is being pushed past its grip: the
rear axle sliding or spinning under power, the front axle locking in the braking
zone or running wide, or the handbrake. The strips are kept as small static
meshes (``CHUNK`` quads each) in a ring, so a long session costs nothing per
frame and the oldest marks are the first to go.

Settings: ``settings.skid_marks`` (live).
"""
from __future__ import annotations

import math

import numpy as np
from panda3d.core import TransparencyAttrib
from ursina import Entity, Mesh, destroy, scene

from . import config

CHUNK = 40                  # quads per mesh
MAX_CHUNKS = 70             # ~2800 quads kept
WIDTH = 0.30                # m
STEP = 0.6                  # metres between points on a strip
REAR_Z, FRONT_Z = -1.42, 0.96
BLACK = (0.02, 0.02, 0.025)
REBUILD_EVERY = 0.12        # s, for the mesh that is still filling


class _Chunk:
    def __init__(self, parent):
        self.verts, self.cols, self.tris = [], [], []
        self.entity = Entity(parent=parent, unlit=True)
        self.entity.setTransparency(TransparencyAttrib.M_alpha)
        self.entity.set_depth_write(False)
        self.entity.set_two_sided(True)
        self.entity.set_bin("fixed", 10)
        self.dirty = False

    @property
    def full(self) -> bool:
        return len(self.verts) >= 4 * CHUNK

    def add(self, quad, alpha_a, alpha_b):
        k = len(self.verts)
        self.verts += quad
        c = BLACK
        self.cols += [(*c, alpha_a), (*c, alpha_a), (*c, alpha_b), (*c, alpha_b)]
        self.tris += [k, k + 1, k + 2, k, k + 2, k + 3]
        self.dirty = True

    def rebuild(self):
        if self.dirty and self.verts:
            self.entity.model = Mesh(vertices=self.verts, triangles=self.tris,
                                     colors=self.cols, mode="triangle", static=True)
        self.dirty = False

    def destroy(self):
        destroy(self.entity)


class SkidMarks:
    def __init__(self):
        self.root = Entity(parent=scene)
        self.chunks: list[_Chunk] = []
        self._wheel: dict = {}            # (key, wheel) -> (x, z, left, right) of the last point
        self._since = 0.0
        self.enabled = True

    # -- drawing ----------------------------------------------------------
    def _lay(self, key, wheel, x, y, z, alpha):
        prev = self._wheel.get((key, wheel))
        if prev is not None:
            px, pz = prev[0], prev[1]
            d = math.hypot(x - px, z - pz)
            if d < STEP:
                return
            if d > 6.0:                   # a teleport (reset), not a mark
                prev = None
        if prev is None:
            self._wheel[(key, wheel)] = (x, z, None, None)
            return
        dx, dz = (x - prev[0]) / d, (z - prev[1]) / d
        nx, nz = -dz * WIDTH / 2, dx * WIDTH / 2
        l1, r1 = (x - nx, z - nz), (x + nx, z + nz)
        l0, r0 = prev[2], prev[3]
        if l0 is None:
            l0, r0 = (prev[0] - nx, prev[1] - nz), (prev[0] + nx, prev[1] + nz)
        if not self.chunks or self.chunks[-1].full:
            self.chunks.append(_Chunk(self.root))
            if len(self.chunks) > MAX_CHUNKS:
                self.chunks.pop(0).destroy()
        ch = self.chunks[-1]
        a0 = prev[4] if len(prev) > 4 else alpha
        ch.add([(l0[0], y, l0[1]), (r0[0], y, r0[1]),
                (r1[0], y, r1[1]), (l1[0], y, l1[1])], a0, alpha)
        self._wheel[(key, wheel)] = (x, z, l1, r1, alpha)

    def _release(self, key, wheel):
        self._wheel.pop((key, wheel), None)

    # -- per frame ----------------------------------------------------------
    def update(self, dt: float, cars) -> None:
        """*cars*: (key, vehicle, car, ctl-or-None) for each car to mark."""
        if not self.enabled:
            return
        for key, v, car, ctl in cars:
            self._car(key, v, car, ctl)
        self._since += dt
        if self._since >= REBUILD_EVERY and self.chunks:
            self._since = 0.0
            self.chunks[-1].rebuild()
            if len(self.chunks) > 1:
                self.chunks[-2].rebuild()        # a chunk that has just filled

    def _car(self, key, v, car, ctl):
        speed = v.speed
        if v.frozen or speed < 8.0 or not v.on_track:
            for w in range(4):
                self._release(key, w)
            return
        t = v.tele
        slide = abs(math.degrees(v.slip_angle))
        brake = ctl.brake if ctl is not None else 0.0
        hand = ctl is not None and ctl.handbrake
        # intensity 0..1 per axle
        rear = max(0.0, min(1.0, (slide - 6.0) / 14.0))
        if t.saturated_rear:
            rear = max(rear, 0.45)
        if hand:
            rear = max(rear, 0.9)
        if getattr(v, "tc_cut", 0.0) > 0.25:        # wheelspin
            rear = max(rear, 0.35 + 0.4 * v.tc_cut)
        front = 0.0
        if brake > 0.9 and speed > 14.0:
            front = 0.85 if not v.abs_enabled else 0.22 * min(1.0, speed / 70.0)
        if t.saturated_front:
            front = max(front, 0.5)
        yaw = math.radians(car.rotation_y)
        c, s = math.cos(yaw), math.sin(yaw)
        y = car.y + config.Y_LINE
        half = config.WHEEL_HALF_TRACK
        for w, (side, zf, amt) in enumerate(((-1, REAR_Z, rear), (1, REAR_Z, rear),
                                              (-1, FRONT_Z, front), (1, FRONT_Z, front))):
            if amt < 0.15:
                self._release(key, w)
                continue
            lx, lz = side * half, zf
            wx = car.x + lx * c + lz * s
            wz = car.z - lx * s + lz * c
            self._lay(key, w, wx, y, wz, min(0.85, 0.25 + 0.6 * amt))

    def destroy(self) -> None:
        for ch in self.chunks:
            ch.destroy()
        self.chunks.clear()
        destroy(self.root)
