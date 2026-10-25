"""Is a pass technically possible right now? Asked of the game's own physics.

A car is behind another. The field is copied (``fork``) and run a few seconds
ahead once per way of trying: the car as its own driver has it ("stay");
committed to a lane either side of the car ahead at full pace, back onto its
line once clear ahead or given up before the next corner if not alongside
("left" / "right", ``Commit``); and in the tow, then pulling out into the next
braking zone on its inside or outside, racecraft's pass with none of its gates
("inside" / "outside", ``Attack``). Everyone else drives as they would,
the car ahead defending if its driver does. A way of trying *works* if the car
ends clear ahead of the other with no contact, no recovery (anyone's), no
warning or penalty and no track-limits strike of its own.

The answer is a ``Probe``: what each way of trying came to. A pass was
*possible* if some lane worked; *missed* if one worked and staying did not.
That is a definition of "possible" no rule wrote: the physics, the other
drivers and the stewards say so. It is for training and measuring, not for
the game (one probe costs several seconds of simulation).

Copies leave out the cars far from the two (``keep``) so that a probe costs
what the few cars around cost; everything indexed by car is renumbered.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field as dfield

import numpy as np

from . import config, raceai

DT = 1.0 / 60.0
#: Seconds each way of trying is run for: long enough for a tow down a
#: straight into the next braking zone.
HORIZON = 15.0
#: Clear ahead: metres of race distance in front of the other car's.
CLEAR = config.CAR_BODY_LENGTH + 1.5
#: ...and further ahead than this, back onto the line.
REJOIN = config.CAR_BODY_LENGTH + 4.0
#: Lateral room between the two cars' centres for a lane to count as beside.
SIDE_ROOM = config.CAR_BODY_WIDTH + 0.6
#: Pace asked for while trying (raceai.PACES' top).
PACE = max(raceai.PACES)
#: Cars kept in a copy: this far behind the trailing / ahead of the leading of
#: the two (metres).
KEEP_BEHIND, KEEP_AHEAD = 150.0, 450.0


# --------------------------------------------------------------------------
# copying the field
# --------------------------------------------------------------------------
def _static(fld) -> dict:
    """A deepcopy memo that shares what nothing changes during a race: the
    track, the plans, the networks and the big lookup tables."""
    memo = {}

    def share(o):
        if o is not None:
            memo[id(o)] = o

    share(fld.track)
    fr = fld.frame
    for o in (fr.ref, fr.arc, fr._arc_l, fr._bucket, fr._center_l, fr._tan_l,
              fld._ref_n, fld._ref_v, fld._curv):
        share(o)
    for e in fld.cars:
        for part in (e.surface, e.limits):
            for k, v in vars(part).items():
                if isinstance(v, (list, np.ndarray)) and len(v) > 200 and k != "_prev":
                    share(v)
        d = e.driver
        if d is None:
            continue
        share(d.plan)
        share(d.to_brake)
        # The deciders themselves: networks hold no state, and a trainer's
        # exploring hooks (with their trajectories) are swapped out of a copy
        # anyway (``fork``'s *net*), so neither is worth copying.
        share(getattr(d.follow, "drive", None))
        share(d.policy)
        share(getattr(d.policy, "net", None))
    return memo


def _remap_key(k, m):
    if isinstance(k, tuple):
        return tuple(m[x] for x in k)
    return m[k]


def _keys_in(k, m) -> bool:
    return all(x in m for x in k) if isinstance(k, tuple) else k in m


def fork(fld, keep=None, net=None):
    """A copy of the field to run ahead in, sharing only what never changes.
    *keep*: the car indices to copy (None: all); the copy's cars are then
    renumbered in order. *net*: a ``onenet.OneNet`` that drives and decides for
    every AI car of the copy, greedily (for a trainer, whose cars drive with
    exploring hooks). Returns ``(copy, {old index: new})``."""
    f2 = copy.deepcopy(fld, _static(fld))
    if net is not None:
        from .onenet import OnePolicy
        for e in f2.cars:
            if e.driver is not None:
                e.driver.follow.drive = net
                e.driver.policy = OnePolicy(net)
    n = len(fld.cars)
    if keep is None:
        return f2, {i: i for i in range(n)}
    keep = sorted(set(int(i) for i in keep))
    m = {old: new for new, old in enumerate(keep)}
    f2.cars = [f2.cars[i] for i in keep]
    f2.views = [f2.views[i] for i in keep] if len(f2.views) == n else []
    for e in f2.cars:
        e.idx = m[e.idx]
        d = e.driver
        if d is None:
            continue
        d.idx = e.idx
        d.leader = m.get(d.leader) if d.leader is not None else None
        if d.attack is not None:
            d.attack = (m[d.attack[0]],) + tuple(d.attack[1:]) if d.attack[0] in m else None
        lp = getattr(d, "lead_pace", None)
        if isinstance(lp, dict):
            d.lead_pace = {m[k]: v for k, v in lp.items() if k in m}
        d._ocap = None
    for v in f2.views:
        v.idx = m[v.idx]
    f2.frame._pred = {}
    f2._order_pairs = {_remap_key(k, m): v for k, v in f2._order_pairs.items() if _keys_in(k, m)}
    f2._trouble_since = {m[k]: v for k, v in f2._trouble_since.items() if k in m}
    if "_last_rec" in f2.__dict__:
        f2._last_rec = {m[k]: v for k, v in f2._last_rec.items() if k in m}
    if getattr(f2, "_trouble_now", None) is not None:
        f2._trouble_now = [f2._trouble_now[i] for i in keep]
    f2.events = []
    rc = f2.rc
    rc.cars = [rc.cars[i] for i in keep]
    for r in rc.cars:
        r.last_contact_with = m.get(r.last_contact_with, -1)
    rc._pair_t = {_remap_key(k, m): v for k, v in rc._pair_t.items() if _keys_in(k, m)}
    pend = []
    for p in rc._pending:
        if p.fault in m and p.victim in m:
            p.fault, p.victim = m[p.fault], m[p.victim]
            pend.append(p)
    rc._pending = pend
    rc._yellow_passes = [[t, m[a], m[b]] for t, a, b in rc._yellow_passes if a in m and b in m]
    rc.messages = []
    return f2, m


def nearby(fld, cars, behind: float = KEEP_BEHIND, ahead: float = KEEP_AHEAD) -> list[int]:
    """Indices of the cars between *behind* metres behind the rearmost of
    *cars* and *ahead* metres ahead of the foremost (by lap distance)."""
    fr, views = fld.frame, fld.views
    ref = views[cars[0]].s
    rel = [fr.ds(ref, views[c].s) for c in cars]       # metres each is ahead of ref
    lo, hi = min(rel) - behind, max(rel) + ahead
    out = [o.idx for o in views if lo <= fr.ds(ref, o.s) <= hi]
    return sorted(set(out) | set(cars))


# --------------------------------------------------------------------------
# trying
# --------------------------------------------------------------------------
#: A lane try gives up -- back onto the line, behind -- when the next braking
#: zone is this close (m) and it is not at least this far alongside (m, its
#: nose against the other's: -CAR_BODY_LENGTH is nose to tail) ...
ABORT_BEFORE = 40.0
ABORT_LEAD = -0.5 * config.CAR_BODY_LENGTH
#: ... or when it is in a corner this tight (1/m) and not ahead.
ABORT_KAPPA = 6e-3
#: An attack try sits in the tow (on its line, full pace) until the braking
#: zone it attacks into is this close (m) or the other car this close (m,
#: nose to nose) -- racecraft's own pull-out -- and attacks only into a zone at
#: least ATTACK_MIN (m) away.
ATTACK_PULL = 200.0
ATTACK_PULL_GAP = 10.0 + config.CAR_BODY_LENGTH
ATTACK_MIN = 60.0


def _other(field, idx):
    return next((v for v in field if v.idx == idx), None)


class _Try:
    """A way of trying, installed as a driver's decision policy in a copy;
    once over (``done``) the driver's own policy decides again."""

    def __init__(self, other: int = -1, own=None):
        self.other, self.own = other, own
        self.done = False

    def __call__(self, driver, me, field):
        if self.done:
            if self.own is not None:
                self.own(driver, me, field)
            return
        driver.follow._dec_obs = raceai.observe(driver, me, field)
        self.tick(driver, me, field)

    def finish(self, driver, me, field):
        self.done = True
        raceai.apply_action(driver, me, raceai.DEFAULT_ACTION, field)


class Commit(_Try):
    """Hold *lane* at full pace until clear ahead of the other car, then back
    onto the line. Given up -- back onto the line behind -- if not alongside by
    the next braking zone, or into a real corner while still behind."""

    def __init__(self, lane: float, other: int = -1, own=None):
        super().__init__(other, own)
        self.lane = lane

    def tick(self, driver, me, field):
        o = _other(field, self.other)
        ahead = driver.frame.ds(o.s, me.s) if o is not None else 1e9   # >0: me ahead
        k = driver.frame.node(me.s)
        if ahead > REJOIN:
            return self.finish(driver, me, field)
        if (driver.to_brake[k] < ABORT_BEFORE and ahead < ABORT_LEAD) \
                or (abs(float(driver.plan.kappa[k])) > ABORT_KAPPA and ahead < 0.0):
            return self.finish(driver, me, field)
        raceai.apply_action(driver, me, raceai.encode(self.lane, PACE), field)


class Attack(_Try):
    """Racecraft's pass into a braking zone, with none of its gates (the
    straight, the zone's worth, being held long enough): sit in the tow at full
    pace, pull out on the *inside* (or outside) of the coming corner when the
    zone or the car is close, and let racecraft's own machinery carry it --
    alongside by the zone's settle point, else tuck in behind
    (``RaceDriver._attack_continue``)."""

    def __init__(self, inside: bool, other: int = -1, own=None):
        super().__init__(other, own)
        self.inside = inside
        self.on = False

    def tick(self, driver, me, field):
        if self.on:
            if driver.attack is None:              # over, either way
                self.done = True
            return                                 # a lane action would end it
        o = _other(field, self.other)
        fr = driver.frame
        if o is None or fr.ds(me.s, o.s) <= 0.0 or not len(driver.brake_zones):
            return self.finish(driver, me, field)      # (an oval has no zone to dive into)
        k = fr.node(me.s)
        dist = float(driver.to_brake[k])
        gap = fr.ds(me.s, o.s)
        if dist > ATTACK_MIN and (dist <= ATTACK_PULL or gap <= ATTACK_PULL_GAP):
            z = int(min(driver.brake_zones, key=lambda z: abs((fr.arc[z] - fr.arc[k]) % fr.L - dist)))
            n_abs = driver._attack_begin(o, z, (self.inside,))
            if n_abs is None:                      # no room on that side
                return self.finish(driver, me, field)
            driver.abs_lane = (me.s, me.n, n_abs, max(driver.ABS_BLEND, 2.0 * me.v))
            driver.lane_tick = driver.ticks
            driver.pace_mult = PACE
            self.on = True
            return
        raceai.apply_action(driver, me, raceai.encode(0.0, PACE), field)


def side_lanes(fld, i: int, j: int) -> dict:
    """The lane offsets (raceai.LANES, off car *i*'s own line) nearest its line
    that put it beside car *j* on either side, clipped to the road;
    {"left": d, "right": d} for the sides there is room on."""
    d = fld.cars[i].driver
    me, o = fld.views[i], fld.views[j]
    k = d.frame.node(me.s)
    ko = d.frame.node(o.s)
    o_dev = o.n - float(d.plan.n_raw[ko])            # j, off i's line where j is
    out = {}
    for name, sign in (("left", -1.0), ("right", 1.0)):
        for lane in sorted((l for l in raceai.LANES if l * sign > 0), key=abs):
            c = d._clip_offset(k, lane)
            if (c - o_dev) * sign >= SIDE_ROOM:
                out[name] = lane
                break
    return out


@dataclass
class Outcome:
    passed: bool = False          # clear ahead of the other at the end
    clean: bool = True
    hits: int = 0                 # contacts the car was in
    recoveries: int = 0           # anyone's, in the copy
    events: int = 0               # its warnings and penalties
    strikes: int = 0
    gain_m: float = 0.0           # its race distance over the run
    lead_m: float = 0.0           # metres ahead of the other at the end (<0: behind)
    pass_t: float | None = None   # seconds to clear ahead, if it did
    yellow: bool = False          # owes a place taken under yellow at the end

    @property
    def works(self) -> bool:
        return self.passed and self.clean


@dataclass
class Probe:
    car: int
    other: int
    t: float
    s: float
    gap: float                    # metres the other is ahead
    v: float
    v_other: float
    lanes: dict = dfield(default_factory=dict)
    out: dict = dfield(default_factory=dict)       # way -> Outcome

    @property
    def possible(self) -> bool:
        return any(o.works for w, o in self.out.items() if w != "stay")

    @property
    def missed(self) -> bool:
        return self.possible and "stay" in self.out and not self.out["stay"].passed

    def best(self) -> str | None:
        ok = [(o.gain_m, w) for w, o in self.out.items() if w != "stay" and o.works]
        return max(ok)[1] if ok else None


def lead_of(fld, i: int, j: int) -> float:
    """Metres car *i* is ahead of car *j* along the lap (<0: behind), to the
    centimetre (race distance moves in whole samples), for cars less than half
    a lap apart."""
    return fld.frame.ds(fld.views[j].s, fld.views[i].s)


def _run(fld, i: int, j: int, policy, horizon: float, keep, net=None) -> Outcome:
    f2, m = fork(fld, keep, net)
    a, b = m[i], m[j]
    e = f2.cars[a]
    d = e.driver
    if policy is not None:
        policy.own = d.policy
        policy.other = b
        d.policy = policy
    rc = f2.rc.cars[a]
    p0 = e.limits.progress or 0.0
    ev0 = rc.collision_warnings + len(rc.penalties) + rc.yellow_warnings
    st0, h0 = rc.strikes, e.hits
    rec0 = sum(c.driver.recoveries for c in f2.cars if c.driver is not None)
    out = Outcome()
    steps = int(round(horizon / DT))
    lead = 0.0
    for _ in range(steps):
        f2.step(DT)
        lead = lead_of(f2, a, b)
        if out.pass_t is None and lead > CLEAR:
            out.pass_t = round(f2.t - fld.t, 2)
    out.hits = e.hits - h0
    out.recoveries = sum(c.driver.recoveries for c in f2.cars if c.driver is not None) - rec0
    out.events = rc.collision_warnings + len(rc.penalties) + rc.yellow_warnings - ev0
    out.strikes = rc.strikes - st0
    out.gain_m = round((e.limits.progress or 0.0) - p0, 1)
    out.lead_m = round(lead, 1)
    out.passed = out.lead_m > CLEAR
    # A place taken under yellow and not yet given back is a penalty the run
    # was too short to see (racecontrol.GIVE_BACK_T seconds to give it back).
    out.yellow = any(yp[1] == a for yp in f2.rc._yellow_passes)
    out.clean = (out.hits == 0 and out.recoveries == 0 and out.events == 0 and out.strikes == 0
                 and not out.yellow)
    return out


#: The ways of trying, by name.
WAYS = ("stay", "left", "right", "inside", "outside")


def probe(fld, i: int, j: int, horizon: float = HORIZON, prune: bool = True,
          ways=WAYS, net=None) -> Probe:
    """What staying and each way of trying (*ways*, of ``WAYS``) come to for
    car *i* behind car *j*, run *horizon* seconds ahead in copies of *fld*
    (left untouched). *net*: see ``fork`` -- needed when the field's cars are
    driven by a trainer's hooks."""
    me, o = fld.views[i], fld.views[j]
    p = Probe(i, j, round(fld.t, 2), round(me.s, 1), round(fld.frame.ds(me.s, o.s), 1),
              round(me.v, 2), round(o.v, 2))
    keep = nearby(fld, [i, j]) if prune else None
    if "stay" in ways:
        p.out["stay"] = _run(fld, i, j, None, horizon, keep, net)
    p.lanes = {w: lane for w, lane in side_lanes(fld, i, j).items() if w in ways}
    for way, lane in p.lanes.items():
        p.out[way] = _run(fld, i, j, Commit(lane), horizon, keep, net)
    for way, inside in (("inside", True), ("outside", False)):
        if way in ways:
            p.out[way] = _run(fld, i, j, Attack(inside), horizon, keep, net)
    return p


#: Held up: the car ahead runs below this share of the speed this car took at
#: that point on its previous lap (``Entrant.trace``) -- of its plan at its pace
#: where it has no lap yet. The plan alone overstated it: in a race every car
#: runs a little below its plan, the one ahead as well.
HELD_RATIO = 0.95


def held_up(fld, i: int, max_gap: float = 60.0) -> int | None:
    """The car that car *i* is held up behind right now, if any: the nearest
    racing car ahead within *max_gap* metres and nearly in line, running
    slower than *i* would there (``HELD_RATIO``)."""
    e = fld.cars[i]
    d = e.driver
    if d is None or d.mode != "race" or d.yellow:
        return None                 # under a yellow flag nobody is held up: it is the rule
    fr, me = fld.frame, fld.views[i]
    best = None
    for o in fld.views:
        if o.idx == i or not o.racing or o.ghost:
            continue
        gap = fr.ds(me.s, o.s)
        if 0.0 < gap < max_gap and abs(o.n - me.n) < SIDE_ROOM                 and (best is None or gap < best[0]):
            best = (gap, o)
    if best is None:
        return None
    o = best[1]
    k = fr.node(o.s)
    own = float(e.trace[k]) if e.trace is not None else float("nan")
    if not own == own:
        own = float(d.plan.v[k]) * d.pace_lap
    return o.idx if o.v < HELD_RATIO * own else None


# --------------------------------------------------------------------------
# judging a trainee
# --------------------------------------------------------------------------
class Judge:
    """Missed passes, for a reward. Every *every* seconds each of *cars* that
    is held up (``held_up``) and not already being judged is probed -- the ways
    of trying only (*ways*), short (*horizon*), with *net* deciding in the
    copies. If one works, the car has *horizon* seconds in the real run to get
    clear ahead of that car; if it does not, that is one ``missed`` -- the
    count a trainer's reward reads, as it reads contacts. ``chances`` counts
    the probes that found a way, ``taken`` the ones the car then made good.

    The real run is the "stay" of the probe: what the trainee in fact did,
    exploring as it does, is what is judged.
    """

    def __init__(self, cars, net=None, every: float = 1.5, horizon: float = 8.0,
                 ways=("left", "right", "inside"), max_probes: int = 40):
        self.cars = list(cars)
        self.net, self.every, self.horizon, self.ways = net, every, horizon, tuple(ways)
        self.max_probes = max_probes
        self.next_t = {}
        self.pending = {}                 # car -> (other, deadline)
        self.missed = {c: 0 for c in self.cars}
        self.chances = 0
        self.taken = 0
        self.probes = 0

    def update(self, fld) -> None:
        """Call after every physics step."""
        for c, (o, deadline) in list(self.pending.items()):
            if lead_of(fld, c, o) > CLEAR:
                self.taken += 1
                del self.pending[c]
            elif fld.t >= deadline or not fld.views[o].racing:
                if fld.t >= deadline:
                    self.missed[c] += 1
                del self.pending[c]
        if self.probes >= self.max_probes:
            return
        for c in self.cars:
            if c in self.pending or fld.t < self.next_t.get(c, 0.0):
                continue
            j = held_up(fld, c)
            if j is None:
                continue
            self.next_t[c] = fld.t + self.every
            self.probes += 1
            p = probe(fld, c, j, self.horizon, ways=self.ways, net=self.net)
            if p.possible:
                self.chances += 1
                self.pending[c] = (j, fld.t + self.horizon)
            if self.probes >= self.max_probes:
                return
