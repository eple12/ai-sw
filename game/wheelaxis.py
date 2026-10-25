"""Which way a baked wheel's axle really points.

The cars' wheels are modelled with their static camber: the front ones lean 4 deg
in on the baked cars (``rl_`` / ``rc_``), and some rears are off by a degree or
more. The wheel spins about the hub's x axis, so a leaning wheel turned about the
wrong line wobbles from side to side as it rolls, like a bent rim. The line to
spin it about is the axis of its tread, found here: a cylinder fitted to the
outermost vertices.

Numpy only. ``fit`` takes the vertices of one wheel in the hub's frame and
returns the axle's direction and a point on it.
"""
from __future__ import annotations

import math

import numpy as np

#: Share of the largest radius from which a vertex counts as tread.
TREAD = 0.96


def _axis(th: float, ph: float) -> np.ndarray:
    return np.array((math.cos(th), math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph)))


def fit(V: np.ndarray):
    """``(point, axis, radius, residual sd)`` of the cylinder through the tread
    of the vertices *V* (N x 3), the axis pointing towards +x, or None when
    there is too little of a wheel to fit."""
    V = np.asarray(V, dtype=float)
    if len(V) < 50:
        return None
    c0 = V.mean(axis=0)
    ev, evec = np.linalg.eigh(np.cov((V - c0).T))
    # The axle is the direction the spread differs most in: a disc or a short
    # cylinder has two equal principal moments and one apart.
    apart = [abs(ev[i] - ev[j]) + abs(ev[i] - ev[k]) for i, j, k in ((0, 1, 2), (1, 0, 2), (2, 0, 1))]
    a0 = evec[:, int(np.argmax(apart))]
    a0 = a0 * (1.0 if a0[0] >= 0.0 else -1.0)
    d = V - c0
    r = np.linalg.norm(d - np.outer(d @ a0, a0), axis=1)
    T = V[r > TREAD * r.max()]
    if len(T) < 20:
        return None

    def residual(x):
        a = _axis(x[0], x[1])
        d = T - np.array((0.0, x[2], x[3]))
        return np.linalg.norm(d - np.outer(d @ a, a), axis=1) - x[4]

    # Gauss-Newton with a numerical Jacobian: five parameters (the axis'
    # two angles, where it crosses the x = 0 plane, the radius).
    x = np.array((math.acos(min(1.0, a0[0])), math.atan2(a0[2], a0[1]), c0[1], c0[2], float(r.max())))
    for _ in range(30):
        f = residual(x)
        J = np.empty((len(f), 5))
        for j in range(5):
            h = 1e-6
            xp = x.copy()
            xp[j] += h
            J[:, j] = (residual(xp) - f) / h
        step = np.linalg.lstsq(J, -f, rcond=None)[0]
        x = x + step
        if float(np.abs(step).max()) < 1e-9:
            break
    f = residual(x)
    return np.array((0.0, x[2], x[3])), _axis(x[0], x[1]), float(x[4]), float(np.std(f))


def basis(axis: np.ndarray) -> np.ndarray:
    """A proper rotation (rows are the images of the x, y and z axes) that turns
    x onto *axis*, with the smallest swing."""
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    h = np.array((0.0, 1.0, 0.0)) if abs(a[1]) < 0.9 else np.array((0.0, 0.0, 1.0))
    r1 = h - (h @ a) * a
    r1 = r1 / np.linalg.norm(r1)
    r2 = np.cross(a, r1)
    return np.vstack((a, r1, r2))
