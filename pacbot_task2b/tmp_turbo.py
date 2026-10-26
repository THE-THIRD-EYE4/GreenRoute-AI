"""PB Task 2B controller - v5 (built on v4 / Task 2A driving).

What changed from v4, and why
  v4 problem                                   v5 fix
  -------------------------------------------  -------------------------------------------
  front wall "too close" -> the run was         never pretend to have arrived: stop, back off,
  ACCEPTED as arrived although the bot was     re-localise from the ToF sensors against the
  short of the cell, so the whole rest of the  map, then re-plan from the cell the bot is
  plan ran from a wrong position and hit the   really in
  same wall again
  pose corrections rejected any error > 4 cm,   large errors are applied too, but only when
  so a big drift was never fixed               several frames in a row agree on them
  run / turn timeouts "accepted" and carried    every watchdog (stall, timeout, wall guard)
  on blindly -> bot stuck in one place          goes through one recovery routine: back off,
                                               re-localise, re-centre, re-plan
  same wall hit again and again                 an edge that caused 2 recoveries is marked
                                               blocked and the planner routes around it
  gate timing per whole cell with big margins:  timing plan: our own motion is simulated in
  rarely a clear window -> "best effort" ->     20 ms steps and checked against the learned
  caught; ghost close -> stop in its path       ghost loop at every instant (also for the bot
                                               being 10-30 % slower/faster than planned)
  no window ever -> wait, try, retreat, retry   in-and-out plans: try every entrance to the
  the same route forever                        ghost's cells, dash in and reverse/turn out;
                                               if a pellet is truly unreachable, skip it and
                                               exit (10 marks beat 0)
  ghost closes in anyway                        escape planner: simulate carry-on / reverse /
                                               run to each nearby safe cell / stay, take the
                                               one with the largest clearance
  "out" mode drove east forever if the plan     "out" only after the exit run; missed pellets
  ended early; only 2 re-plans allowed          are re-planned (with a nudge onto the pellet)
  slow pivots, long pauses                      profiled pivots (accel-limited), shorter
                                               settles, fast exit straight

Pipeline
  1. OBSERVE (free: the run clock starts only when the bot first moves)
     learn the ghost's loop, its danger cells and the gyro bias.
  2. PLAN  Dijkstra on (cell, heading) with time-based costs (cell time, stop+pivot
     time), all 6 pellet orders, penalties for the ghost's cells.
  3. DRIVE straight runs (merged cells, trapezoid speed profile, wall-following
     correction) and on-the-spot pivots at cell centres; gates before the ghost's
     cells; recovery on any anomaly.

Set SPEED_PROFILE below. Start with "normal"; use "safe" if you see collisions,
"fast" only after several clean runs.
"""
import bisect
import heapq
import itertools
import json
import math
import time

import paho.mqtt.client as mqtt

import maze_2b
from maze_2b import Maze

MQTT_HOST = "localhost"
MQTT_PORT = 1883
TOPIC_SENSORS = "pacbot/sensors"
TOPIC_PELLETS = "pacbot/pellets"
TOPIC_GHOST = "pacbot/ghost"
TOPIC_RESULT = "pacbot/result"
TOPIC_WHEEL_VEL = "pacbot/wheel_vel"

# ---------------------------------------------------------------- geometry
N = 15
PITCH = 0.22
WALL_HALF = 0.005
WALL_FACE = PITCH / 2 - WALL_HALF   # 0.105: cell centre -> wall face
WHEEL_RADIUS = 0.017
HALF_TRACK = 0.039
WHEEL_LIMIT = 30.0
TICKS_PER_REV = 360
M_PER_TICK = 2.0 * math.pi * WHEEL_RADIUS / TICKS_PER_REV
AXLE_BACK = 0.033          # axle is 33 mm behind chassis centre
SPAWN_X, SPAWN_Y = -0.15, 0.0
EXIT_X = 3.19
DELTA = ((0, 1), (-1, 0), (0, -1), (1, 0))   # E, N, W, S  (row, col)

# ToF sensors in the chassis frame (from our Task 2A solver): name, (forward, left), angle
SENSORS = (
    ("sl", (-0.005, 0.034), 0.0),
    ("sr", (-0.005, -0.034), 0.0),
    ("fl", (0.036, 0.005), math.pi / 2),
    ("fr", (0.036, -0.005), -math.pi / 2),
)
SENSOR_POS = {n: p for n, p, _ in SENSORS}
TOF_MAX = 2.0
BUMPER = 0.051             # forward ToF origin -> front bumper
FRONT_AT_CENTRE = WALL_FACE - (AXLE_BACK - 0.005)   # 0.077: forward ToF with the axle on the centre

# ---------------------------------------------------------------- speed profile
SPEED_PROFILE = "turbo"   # "safe" | "normal" | "fast"
_PROFILES = {
    "safe":   dict(V_MAX=0.30, V_EXIT=0.36, ACCEL=0.8, DECEL=0.7, W_TURN=4.0, A_TURN=20.0),
    "normal": dict(V_MAX=0.36, V_EXIT=0.45, ACCEL=1.0, DECEL=0.8, W_TURN=5.0, A_TURN=25.0),
    "fast":   dict(V_MAX=0.44, V_EXIT=0.50, ACCEL=1.2, DECEL=1.0, W_TURN=6.0, A_TURN=30.0),
    "turbo":  dict(V_MAX=0.50, V_EXIT=0.50, ACCEL=1.6, DECEL=1.3, W_TURN=7.5, A_TURN=40.0),
}
_P = _PROFILES[SPEED_PROFILE]
V_MAX = _P["V_MAX"]        # m/s cruise
V_EXIT = _P["V_EXIT"]      # m/s on the final straight (no stop needed)
ACCEL = _P["ACCEL"]        # m/s^2
DECEL = _P["DECEL"]        # m/s^2 (braking profile towards a stop)
W_TURN = _P["W_TURN"]      # rad/s peak pivot rate
A_TURN = _P["A_TURN"]      # rad/s^2 pivot deceleration profile

# ---------------------------------------------------------------- driving
V_MIN = 0.03
STOP_TOL = 0.002
LOOK_AHEAD = 0.10
K_HEAD = 6.0
W_DRIVE = 3.0
SIDE_PUSH = 0.055          # side ToF below this -> steer away from that wall directly
K_SIDE_PUSH = 40.0
FRONT_GUARD = BUMPER + 0.012   # forward ToF: bumper 12 mm from something -> stop
FRONT_SLOW = BUMPER + 0.05     # one forward sensor sees something this close -> crawl
W_TURN_MIN = 0.5
TURN_TOL = 0.02
TURN_RATE_DONE = 0.3
PAUSE = 0.06
ALIGN_TOL = 0.004
USE_STOP_FIX = True        # small ToF re-localisation at every stop before a pivot

# pose correction while driving straight
SIDE_GAIN = 0.08
FRONT_GAIN = 0.10
HEAD_GAIN = 0.5            # heading from the slope of a side-wall reading (mount-offset proof)
HEAD_FIT_DIST = 0.08       # m of continuous side wall needed for one heading fit
BIG_FRAMES = 8             # frames that must agree before a large correction is applied
BIG_SPREAD = 0.008         # m, max spread of those frames
BIG_MAX = 0.12             # m, largest correction ever applied in one go

# watchdogs and recovery
STALL_TIME = 0.35          # s of commanded motion without wheel motion
TURN_TIMEOUT = 2.0
ALIGN_TIMEOUT = 1.2
BACKOFF_V = 0.06
BACKOFF_DIST = 0.025
BLOCK_AFTER = 2            # recoveries on the same edge before it is treated as a wall

# ---------------------------------------------------------------- planning (seconds)
_STOP_START = 0.5 * V_MAX / ACCEL + 0.5 * V_MAX / DECEL + PAUSE
_PIVOT90 = (math.pi / 2) / W_TURN + W_TURN / A_TURN + 0.1
PLAN_CELL = PITCH / V_MAX
PLAN_TURN = (0.0, _STOP_START + _PIVOT90, _STOP_START + 2 * _PIVOT90 - 0.1,
             _STOP_START + _PIVOT90)
PLAN_DANGER = 3.0          # per cell inside the ghost's patrol: prefer short visits
PLAN_DANGER_TURN = 1.5

# ---------------------------------------------------------------- ghost
OBSERVE_MIN = 1.0          # s (own clock)
OBSERVE_MAX = 35.0         # s own clock
OBSERVE_MAX_WALL = 40.0    # s wall clock: move well before the evaluator's 60 s watchdog
GHOST_STATIC_WALL = 6.0
DANGER_R = 0.19
MODEL_TOL = 0.08
# Timing plan: the bot's own motion is simulated step by step (VDT) and compared with the
# predicted ghost position at every instant. STRETCH covers the bot being slower / faster
# than predicted; the clearance (chassis centre to ghost centre, metres) must hold for all.
VDT = 0.02
STRETCH = (0.9, 1.0, 1.15, 1.3)
GATE_LEVELS = (                       # ((nominal, stretched) clearance, stretches), relaxed in turn
    ((0.20, 0.15), STRETCH),
    ((0.17, 0.13), STRETCH),
    ((0.15, 0.115), (0.95, 1.0, 1.15)),
    ((0.13, 0.11), (1.0, 1.1)),
    ((0.15, 0.10), (1.0, 1.05)),      # last resort before giving a pellet up
)
WAIT_CLEAR = 0.13          # while waiting at the gate cell
RECHECK_CLEAR = (0.13, 0.11)   # re-check before each action inside the ghost's cells
COMMIT_CLEAR = 0.095       # same, for a crossing that cannot be avoided
THREAT_R = 0.17            # ghost this close and closing -> choose an escape
ESCAPE_DEPTH = 6           # cells searched for a safe cell to escape to
BACK_V = 0.28              # reversing speed (escapes, reversing out of a dash)

# ======================================================================= utils
def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def wheels(v, omega):
    left = (v - omega * HALF_TRACK) / WHEEL_RADIUS
    right = (v + omega * HALF_TRACK) / WHEEL_RADIUS
    m = max(abs(left), abs(right))
    if m > WHEEL_LIMIT:
        left *= WHEEL_LIMIT / m
        right *= WHEEL_LIMIT / m
    return left, right


def unit(d):
    a = d * math.pi / 2.0
    return round(math.cos(a)), round(math.sin(a))


def cell_centre(cell):
    r, c = cell
    return c * PITCH, (14 - r) * PITCH


def cell_of(x, y):
    return int(round(14 - y / PITCH)), int(round(x / PITCH))


def quadrant(th):
    return int(round(th / (math.pi / 2))) % 4


# ======================================================================= maze
def _find_layout():
    """The maze drawing in maze_2b.py (same text format as Task 2A's LAYOUT)."""
    cands = []
    for obj in list(vars(maze_2b).values()) + [getattr(maze_2b.Maze, k, None) for k in dir(maze_2b.Maze)]:
        if isinstance(obj, str) and obj.count("\n") >= 30:
            obj = obj.strip("\n").splitlines()
        if isinstance(obj, (list, tuple)) and len(obj) >= 31 and all(isinstance(x, str) for x in obj):
            lines = [x for x in obj if x.strip()]
            if len(lines) == 31 and lines[0].startswith("+---"):
                cands.append(lines)
    return cands[0] if cands else None


def _layout_walls(L):
    """{(row, col, d): wall?} parsed exactly as in our Task 2A solver."""
    walls = {}
    for row in range(N):
        for col in range(N):
            r, c = 2 * row + 1, 4 * col
            line = L[r].ljust(4 * N + 1)
            walls[row, col, 0] = line[c + 4] == "|"
            walls[row, col, 1] = L[r - 1].ljust(4 * N + 1)[c + 1:c + 4] == "---"
            walls[row, col, 2] = line[c] == "|"
            walls[row, col, 3] = L[r + 1].ljust(4 * N + 1)[c + 1:c + 4] == "---"
    return walls


class MazeView:
    def __init__(self, maze):
        self.m = maze
        self.exit_row = getattr(maze, "exit_row", 7)
        self.walls = None
        self.blocked = set()        # (cell, h) learned while driving
        L = _find_layout()
        if L is not None:
            self.walls = _layout_walls(L)
            for r in range(N):                       # exit opening on the east border
                if not self.walls[r, N - 1, 0]:
                    self.exit_row = r
            bad = 0
            for (r, c, d), w in self.walls.items():
                dr, dc = DELTA[d]
                if not (0 <= r + dr < N and 0 <= c + dc < N):
                    continue
                try:
                    api_open = bool(maze.can_move((r, c), d))
                except Exception:
                    api_open = None
                if api_open is not None and api_open == w:
                    bad += 1
            print(f"maze walls read from LAYOUT; can_move disagrees on {bad} wall sides; "
                  f"exit row {self.exit_row}", flush=True)
        else:
            print("WARNING: no LAYOUT found in maze_2b, using maze.can_move()", flush=True)
        md = getattr(maze, "DELTA", None)
        if md is not None and tuple(tuple(d) for d in md) != DELTA:
            print(f"WARNING: maze.DELTA {md} differs from {DELTA}", flush=True)
        self._wall_cache = {}

    @staticmethod
    def in_bounds(cell):
        return 0 <= cell[0] < N and 0 <= cell[1] < N

    def _map_open(self, cell, h):
        dr, dc = DELTA[h]
        if not self.in_bounds(cell) or not self.in_bounds((cell[0] + dr, cell[1] + dc)):
            return False
        if self.walls is not None:
            return not self.walls[cell[0], cell[1], h]
        return bool(self.m.can_move(cell, h))

    def can_move(self, cell, h, use_blocked=True):
        if use_blocked and (cell, h) in self.blocked:
            return False
        return self._map_open(cell, h)

    def block(self, cell, h):
        dr, dc = DELTA[h]
        self.blocked.add((cell, h))
        self.blocked.add(((cell[0] + dr, cell[1] + dc), (h + 2) % 4))

    def wall(self, cell, h):
        """True if there is a physical wall on side h of an in-bounds cell (map only)."""
        key = (cell, h)
        w = self._wall_cache.get(key)
        if w is None:
            if not self.in_bounds(cell):
                w = False
            elif h == 0 and cell == (self.exit_row, N - 1):
                w = False
            elif h == 2 and cell == (N - 1, 0):
                w = False
            else:
                w = not self._map_open(cell, h)
            self._wall_cache[key] = w
        return w

    def edge_wall(self, cell, h):
        """Wall between `cell` and its neighbour in direction h (either may be outside)."""
        if self.in_bounds(cell):
            return self.wall(cell, h)
        dr, dc = DELTA[h]
        nb = (cell[0] + dr, cell[1] + dc)
        if self.in_bounds(nb):
            return self.wall(nb, (h + 2) % 4)
        return False

    # ------------------------------------------------------------- ToF model
    def raycast(self, px, py, ang, max_d=TOF_MAX):
        """Distance from (px, py) along `ang` to the first wall face in the map."""
        dx, dy = math.cos(ang), math.sin(ang)
        gx = (px + PITCH / 2) / PITCH
        gy = (py + PITCH / 2) / PITCH
        ci, cj = math.floor(gx), math.floor(gy)      # column, and "north index" 14 - row
        if abs(dx) < 1e-9:
            tx, sx_, ddx = float("inf"), 0, float("inf")
        else:
            sx_ = 1 if dx > 0 else -1
            bx = (ci + (1 if dx > 0 else 0)) * PITCH - PITCH / 2
            tx, ddx = (bx - px) / dx, PITCH / abs(dx)
        if abs(dy) < 1e-9:
            ty, sy_, ddy = float("inf"), 0, float("inf")
        else:
            sy_ = 1 if dy > 0 else -1
            by = (cj + (1 if dy > 0 else 0)) * PITCH - PITCH / 2
            ty, ddy = (by - py) / dy, PITCH / abs(dy)
        for _ in range(64):
            if tx < ty:
                if tx > max_d:
                    return max_d
                if self.edge_wall((14 - cj, ci), 0 if sx_ > 0 else 2):
                    return max(0.0, tx - WALL_HALF / abs(dx))
                ci += sx_
                tx += ddx
            else:
                if ty > max_d:
                    return max_d
                if self.edge_wall((14 - cj, ci), 1 if sy_ > 0 else 3):
                    return max(0.0, ty - WALL_HALF / abs(dy))
                cj += sy_
                ty += ddy
        return max_d

    def predict_tof(self, ax, ay, th):
        c, s = math.cos(th), math.sin(th)
        bx, by = ax + AXLE_BACK * c, ay + AXLE_BACK * s
        out = {}
        for name, (sx, sy), a in SENSORS:
            out[name] = self.raycast(bx + sx * c - sy * s, by + sx * s + sy * c, th + a)
        return out


# ======================================================================= planning
def dijkstra_segment(view, start_cell, start_h, goal_cell, goal_h, danger):
    first = (start_cell, start_h)
    best = {first: 0.0}
    prev = {first: None}
    heap = [(0.0, 0, first)]
    tie = 1
    found = None
    while heap:
        cost, _, st = heapq.heappop(heap)
        if cost > best[st]:
            continue
        c, h = st
        if c == goal_cell and (goal_h is None or h == goal_h):
            found = st
            break
        nexts = []
        for nh in range(4):
            if nh != h:
                extra = PLAN_DANGER_TURN if c in danger else 0.0
                nexts.append(((c, nh), PLAN_TURN[(nh - h) % 4] + extra))
        if view.can_move(c, h):
            dr, dc = DELTA[h]
            nc = (c[0] + dr, c[1] + dc)
            nexts.append(((nc, h), PLAN_CELL + (PLAN_DANGER if nc in danger else 0.0)))
        for ns, w in nexts:
            ncost = cost + w
            if ncost < best.get(ns, float("inf")):
                best[ns] = ncost
                prev[ns] = st
                heapq.heappush(heap, (ncost, tie, ns))
                tie += 1
    if found is None:
        return None, float("inf")
    path = []
    s = found
    while s is not None:
        path.append(s)
        s = prev[s]
    return path[::-1], best[found]


class RestrictedView:
    """A MazeView that only allows moves into `allowed` cells."""

    def __init__(self, view, allowed):
        self.view = view
        self.allowed = allowed
        self.exit_row = view.exit_row

    def can_move(self, cell, h):
        dr, dc = DELTA[h]
        return (cell[0] + dr, cell[1] + dc) in self.allowed and self.view.can_move(cell, h)


def plan_route(view, start_cell, start_h, pellets, danger):
    exit_cell = (view.exit_row, N - 1)
    best = (None, None, float("inf"))
    cache = {}

    def seg(a, goal, goal_h):
        key = (a, goal, goal_h)
        if key not in cache:
            cache[key] = dijkstra_segment(view, a[0], a[1], goal, goal_h, danger)
        return cache[key]

    for perm in itertools.permutations(pellets):
        cur = (start_cell, start_h)
        states = [cur]
        total = 0.0
        ok = True
        for p in perm:
            path, cost = seg(cur, p, None)
            if path is None:
                ok = False
                break
            states += path[1:]
            total += cost
            cur = path[-1]
        if not ok:
            continue
        path, cost = seg(cur, exit_cell, 0)
        if path is None:
            continue
        states += path[1:]
        total += cost
        if total < best[2]:
            best = (states + [((view.exit_row, N), 0)], perm, total)
    return best


def build_actions(states, danger):
    prims = []
    for (ca, ha), (cb, hb) in zip(states, states[1:]):
        if ca != cb:
            prims.append(("move", ca, cb, ha))
        elif ha != hb:
            if prims and prims[-1][0] == "turn" and prims[-1][1] == ca:
                prims[-1] = ("turn", ca, prims[-1][2], hb)
            else:
                prims.append(("turn", ca, ha, hb))

    def enters_danger(p):
        return p[0] == "move" and p[1] not in danger and p[2] in danger

    actions = []
    i = 0
    while i < len(prims):
        p = prims[i]
        if p[0] == "turn":
            if p[3] != p[2]:
                actions.append(("turn", p[3], (p[3] - p[2]) % 4))
            i += 1
            continue
        if enters_danger(p):
            ep = []
            k = i
            while k < len(prims):
                ep.append(prims[k])
                end_cell = prims[k][2] if prims[k][0] == "move" else prims[k][1]
                if end_cell not in danger:
                    break
                k += 1
            actions.append(("gate", ep))
        j = i
        while (j + 1 < len(prims) and prims[j + 1][0] == "move"
               and prims[j + 1][3] == p[3] and not enters_danger(prims[j + 1])):
            j += 1
        target = prims[j][2]
        kind = "exit_run" if target[1] >= N else "run"
        actions.append((kind, target, j - i + 1, p[3]))
        i = j + 1
    return actions


def reverse_actions(states):
    """Retrace `states` backwards: reverse along each straight, undo each turn."""
    out = []
    for (ca, ha), (cb, hb) in reversed(list(zip(states, states[1:]))):
        if ca != cb:
            if out and out[-1][0] == "back" and out[-1][3] == ha:
                out[-1] = ("back", ca, out[-1][2] + 1, ha)
            else:
                out.append(("back", ca, 1, ha))
        elif ha != hb:
            out.append(("turn", ha, (ha - hb) % 4))
    return out


# ======================================================================= ghost
class GhostModel:
    """Learns the ghost's periodic loop and predicts its future position."""

    def __init__(self):
        self.samples = []          # (t, x, y)
        self.cells = set()
        self.valid = False
        self.period = None
        self.t0 = None
        self.ts, self.xs, self.ys = [], [], []
        self.last_err = None

    def add(self, t, x, y, cell=None):
        if self.samples and t <= self.samples[-1][0]:
            return
        self.samples.append((t, x, y))
        if cell is not None:
            self.cells.add(cell)

    def travel(self):
        s = self.samples
        return sum(math.hypot(s[i][1] - s[i - 1][1], s[i][2] - s[i - 1][2])
                   for i in range(1, len(s)))

    def try_fit(self):
        """Find the smallest period T with x(t) ~= x(t+T) over the record."""
        s = self.samples
        if len(s) < 200:
            return False
        span = s[-1][0] - s[0][0]
        dt = span / (len(s) - 1)
        if self.travel() < 0.4:
            return False
        stride = max(1, int(round(0.1 / dt)))
        pts = [(s[k][1], s[k][2]) for k in range(0, len(s), stride)]
        n = len(pts)
        step_t = dt * stride
        min_lag = max(1, int(2.0 / step_t))
        best = None
        for lag in range(min_lag, n):
            overlap = n - lag
            if overlap * step_t < max(1.5, 0.25 * lag * step_t):
                break
            err = 0.0
            cnt = 0
            for i in range(0, overlap, 2):
                a, b = pts[i], pts[i + lag]
                err += math.hypot(a[0] - b[0], a[1] - b[1])
                cnt += 1
            err /= cnt
            if err < 0.03:
                far = max(math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1])
                          for i in range(0, lag))
                if far > 0.2:
                    best = lag
                    break
        if best is None:
            return False
        coarse = best * stride
        refined, rerr = coarse, float("inf")
        for lag in range(max(1, coarse - stride * 2), min(len(s) - 50, coarse + stride * 2 + 1)):
            err = 0.0
            cnt = 0
            for i in range(0, len(s) - lag, 3):
                err += math.hypot(s[i][1] - s[i + lag][1], s[i][2] - s[i + lag][2])
                cnt += 1
            err /= max(1, cnt)
            if err < rerr:
                refined, rerr = lag, err
        self.last_err = rerr
        if rerr > 0.025:
            return False
        T = s[refined][0] - s[0][0]
        t0 = s[-1][0] - T                  # template = most recent full period
        k0 = bisect.bisect_left([p[0] for p in s], t0)
        self.ts = [p[0] - t0 for p in s[k0:]]
        self.xs = [p[1] for p in s[k0:]]
        self.ys = [p[2] for p in s[k0:]]
        self.period, self.t0 = T, t0
        self.valid = True
        return True

    def _predict(self, t, t0):
        ph = (t - t0) % self.period
        i = bisect.bisect_left(self.ts, ph)
        if i <= 0:
            return self.xs[0], self.ys[0]
        if i >= len(self.ts):
            return self.xs[-1], self.ys[-1]
        ta, tb = self.ts[i - 1], self.ts[i]
        a = 0.0 if tb == ta else (ph - ta) / (tb - ta)
        return (self.xs[i - 1] + a * (self.xs[i] - self.xs[i - 1]),
                self.ys[i - 1] + a * (self.ys[i] - self.ys[i - 1]))

    def predict(self, t):
        return self._predict(t, self.t0)

    def resync(self, recent):
        if not self.valid or len(recent) < 20:
            return False
        step = max(1, len(self.ts) // 400)
        best = (1e9, None)
        tl = recent[-1][0]
        for i in range(0, len(self.ts), step):
            t0 = tl - self.ts[i]
            err = 0.0
            for t, x, y in recent[::5]:
                px, py = self._predict(t, t0)
                err = max(err, math.hypot(px - x, py - y))
                if err > best[0]:
                    break
            if err < best[0]:
                best = (err, t0)
        if best[0] < 0.04:
            self.t0 = best[1]
            return True
        return False

    def danger_cells(self):
        pts = list(zip(self.ts, self.xs, self.ys)) if self.valid else self.samples
        pts = pts[::3]
        out = set(c for c in self.cells if 0 <= c[0] < N and 0 <= c[1] < N)
        for r in range(N):
            for c in range(N):
                cx, cy = cell_centre((r, c))
                if any(math.hypot(x - cx, y - cy) < DANGER_R for _, x, y in pts):
                    out.add((r, c))
        return out

    def centroid(self):
        pts = self.samples
        if not pts:
            return None
        return (sum(p[1] for p in pts) / len(pts), sum(p[2] for p in pts) / len(pts))


# ======================================================================= controller
class Controller:
    def __init__(self, maze):
        self.maze = maze
        self.view = MazeView(maze)
        self.pellets = []
        self.have_pellets = False
        self.ghost = None
        self.ghost_new = False
        self.model = GhostModel()
        self.recent = []
        self.bad_pred = 0
        self.danger = set()
        self.guarded_pellet = None

        self.clock = 0.0            # own clock: sum of sensor dt (sim t is 0 before the start)
        self.t_move = None
        self.prev_ticks = None
        self.done = False

        # pose: wheel-axle centre + heading (chassis spawns at SPAWN_X, SPAWN_Y facing east)
        self.x = SPAWN_X - AXLE_BACK
        self.y = SPAWN_Y
        self.th = 0.0
        self.gyro_sum = 0.0
        self.gyro_n = 0
        self.gyro_bias = 0.0

        self.mode = "observe"
        self.obs_t0 = None
        self.obs_wall0 = None
        self.last_fit = -10.0
        self.actions = []
        self.idx = 0
        self.cell = (N - 1, -1)
        self.dir = 0
        self.target = None
        self.exiting = False
        self.v = 0.0
        self.act = None
        self.act_t0 = 0.0
        self.act_pred = None        # predicted duration of the current action
        self.mode_t0 = 0.0
        self.wait = 0.0
        self.cmd = (0.0, 0.0)
        self.stall_t = 0.0
        self.back_left = 0.0
        self.back_sign = -1
        self.recoveries = 0
        self.edge_trouble = {}
        self.centre_goal = None
        self.turn_goal = 0.0
        self.pellet_tries = {}
        self.skip_pellets = set()
        self.pellet_wait = 0.0
        self.big_side = []
        self.big_front = []
        self.pending_fix = False
        self.head_buf = {}
        self.cell_recoveries = {}
        self.gap_hist = []          # recent bot-ghost distances

        # ghost timing
        self.gate = None            # (go_time, episode_end_index) once a window is chosen
        self.gate_t0 = None
        self.ep_end = None          # index of the last action of the current ghost episode
        self.escape_t = -10.0
        self.escapes = 0
        self.tl_scale = 1.15        # real / predicted action durations (learned)
        self.gate_cell = None
        self.gate_escapes = {}      # gate cell -> escapes that followed it
        self.bad_entries = set()    # (safe cell, heading) entries that did not work
        self.inside_tries = 0
        self.commit = False         # crossing that cannot be avoided: escape only if imminent
        self.avoid_tried = False

    # ------------------------------------------------------------- io
    def log(self, text):
        rt = "" if self.t_move is None else f" run {self.clock - self.t_move:6.2f}s"
        print(f"[{self.clock:7.2f}s{rt}] {text}", flush=True)

    def on_pellets(self, msg):
        new = [tuple(c) for c in msg.get("pellets", [])]
        if len(new) < len(self.pellets):
            self.inside_tries = 0
        self.pellets = new
        self.have_pellets = True
        print(f"pellets remaining: {self.pellets}", flush=True)

    def on_ghost(self, msg):
        self.ghost = msg
        self.ghost_new = True

    # ------------------------------------------------------------- pose
    def _odometry(self, d_left, d_right, turn_rate, dt):
        ds = (d_left + d_right) / 2
        mid = self.th + 0.5 * turn_rate * dt
        self.x += ds * math.cos(mid)
        self.y += ds * math.sin(mid)
        self.th = wrap(self.th + turn_rate * dt)

    def _sensor_pos(self, sx, sy):
        c, s = math.cos(self.th), math.sin(self.th)
        bx = self.x + AXLE_BACK * c
        by = self.y + AXLE_BACK * s
        return bx + sx * c - sy * s, by + sx * s + sy * c

    def chassis(self):
        return (self.x + AXLE_BACK * math.cos(self.th),
                self.y + AXLE_BACK * math.sin(self.th))

    def _shift(self, err, ux, uy, gain, buf):
        """Small errors: blend in. Large errors: apply only once BIG_FRAMES agree."""
        if abs(err) < 0.04:
            buf.clear()
            self.x += gain * err * ux
            self.y += gain * err * uy
            return
        if abs(err) > BIG_MAX:
            buf.clear()
            return
        buf.append(err)
        if len(buf) >= BIG_FRAMES:
            if max(buf) - min(buf) < BIG_SPREAD:
                e = sorted(buf)[len(buf) // 2]
                self.x += e * ux
                self.y += e * uy
                self.log(f"pose corrected by {e:+.3f} m (consistent ToF evidence)")
            buf.clear()

    def _correct_sides(self, s):
        ux, uy = unit(self.dir)
        nx, ny = -uy, ux
        tilt = wrap(self.th - self.dir * math.pi / 2)
        if abs(tilt) > 0.2:
            return
        errs = []
        for name, side in (("fl", 1), ("fr", -1)):
            reading = s[name]
            if reading > 0.16:
                self.head_buf[name] = []
                continue
            px, py = self._sensor_pos(*SENSOR_POS[name])
            cell = cell_of(px, py)
            if not self.view.in_bounds(cell):
                continue
            wall_dir = (self.dir + 1) % 4 if side > 0 else (self.dir + 3) % 4
            if not self.view.wall(cell, wall_dir):
                continue
            cx, cy = cell_centre(cell)
            along = (px - cx) * ux + (py - cy) * uy
            if abs(along) > PITCH / 2 - 0.025:
                continue
            self._head_sample(name, side, px * ux + py * uy, reading, tilt)
            lat = (px - cx) * nx + (py - cy) * ny
            lat_meas = side * (WALL_FACE - reading * math.cos(tilt))
            errs.append(lat_meas - lat)
        if errs:
            self._shift(sum(errs) / len(errs), nx, ny, SIDE_GAIN, self.big_side)

    def _head_sample(self, name, side, pos, reading, tilt):
        """Heading from a side wall: the reading changes by -side*sin(true tilt) per metre
        driven. Using only the slope makes it immune to sensor mounting offsets."""
        buf = self.head_buf.setdefault(name, [])
        if buf and (pos - buf[-1][0] < 0.0 or pos - buf[-1][0] > 0.02):
            buf.clear()                       # not moving forward smoothly
        buf.append((pos, reading, tilt))
        if buf[-1][0] - buf[0][0] < HEAD_FIT_DIST:
            return
        n = len(buf)
        mp = sum(b[0] for b in buf) / n
        mr = sum(b[1] for b in buf) / n
        sxx = sum((b[0] - mp) ** 2 for b in buf)
        slope = sum((b[0] - mp) * (b[1] - mr) for b in buf) / sxx
        res = math.sqrt(sum((b[1] - mr - slope * (b[0] - mp)) ** 2 for b in buf) / n)
        est = sum(b[2] for b in buf) / n
        buf.clear()
        if res > 0.0015 or abs(slope) > 0.3:
            return                            # not a clean straight wall
        err = clamp(math.asin(-side * slope) - est, -0.05, 0.05)
        self.th = wrap(self.th + HEAD_GAIN * err)

    def _correct_front(self, s):
        sl, sr = s["sl"], s["sr"]
        if sl > 1.0 or sr > 1.0 or abs(sl - sr) > 0.012:
            return
        ux, uy = unit(self.dir)
        tilt = wrap(self.th - self.dir * math.pi / 2)
        if abs(tilt) > 0.15 or abs(tilt) * max(sl, sr) > 0.06:
            return
        px, py = self._sensor_pos(-0.005, 0.0)
        cell = cell_of(px, py)
        steps = 0
        while self.view.in_bounds(cell) and not self.view.wall(cell, self.dir) and steps < 6:
            cell = (cell[0] + DELTA[self.dir][0], cell[1] + DELTA[self.dir][1])
            steps += 1
        if not self.view.in_bounds(cell) or steps >= 6:
            return
        cx, cy = cell_centre(cell)
        face = cx * ux + cy * uy + WALL_FACE
        mean = (sl + sr) / 2
        along_meas = face - mean * math.cos(tilt)
        err = along_meas - (px * ux + py * uy)
        self._shift(err, ux, uy, FRONT_GAIN, self.big_front)

    def _relocalise(self, s, window, step, headings=(0.0,), log=True):
        """Grid search of the axle position (and small heading offsets) that best explains
        the four ToF readings on the map. Returns True if the pose was updated."""
        if not self.view.in_bounds(cell_of(*self.chassis())):
            return False
        cap = 0.9
        meas = {k: min(s[k], cap) for k in ("sl", "sr", "fl", "fr")}
        if all(v >= cap for v in meas.values()):
            return False

        def score(ax, ay, th):
            pred = self.view.predict_tof(ax, ay, th)
            e = 0.0
            for k, m in meas.items():
                d = m - min(pred[k], cap)
                e += min(d * d, 0.0016)
            return e + 0.02 * ((ax - self.x) ** 2 + (ay - self.y) ** 2)

        best = (score(self.x, self.y, self.th), self.x, self.y, self.th)
        base = best[0]
        n = int(round(window / step))
        for dth in headings:
            th = wrap(self.th + dth)
            for i in range(-n, n + 1):
                for j in range(-n, n + 1):
                    ax, ay = self.x + i * step, self.y + j * step
                    e = score(ax, ay, th)
                    if e < best[0]:
                        best = (e, ax, ay, th)
        _, bx, by, bth = best           # refine around the best at a quarter of the step
        f = step / 4
        for i in range(-3, 4):
            for j in range(-3, 4):
                e = score(bx + i * f, by + j * f, bth)
                if e < best[0]:
                    best = (e, bx + i * f, by + j * f, bth)
        e, ax, ay, th = best
        if e > 4 * 0.012 ** 2 or base - e < 1e-5:
            return False
        if log:
            self.log(f"re-localised by ({ax - self.x:+.3f},{ay - self.y:+.3f}) "
                     f"dth {wrap(th - self.th):+.3f} (fit {math.sqrt(e / 4) * 1000:.1f} mm)")
        self.x, self.y, self.th = ax, ay, th
        return True

    # ------------------------------------------------------------- driving helpers
    def _steer(self, line_cell, s=None):
        ux, uy = unit(self.dir)
        nx, ny = -uy, ux
        cx, cy = cell_centre(line_cell)
        bx, by = self.chassis()
        lat = (bx - cx) * nx + (by - cy) * ny
        want = self.dir * math.pi / 2 - math.atan2(lat, LOOK_AHEAD)
        w = K_HEAD * wrap(want - self.th)
        if s is not None:     # reactive: never scrape a side wall
            if s["fl"] < SIDE_PUSH:
                w -= K_SIDE_PUSH * (SIDE_PUSH - s["fl"])
            if s["fr"] < SIDE_PUSH:
                w += K_SIDE_PUSH * (SIDE_PUSH - s["fr"])
        return clamp(w, -W_DRIVE, W_DRIVE)

    def _hold_heading(self):
        return clamp(K_HEAD * wrap(self.dir * math.pi / 2 - self.th), -W_DRIVE, W_DRIVE)

    def _remaining(self, cell):
        ux, uy = unit(self.dir)
        cx, cy = cell_centre(cell)
        return (cx - self.x) * ux + (cy - self.y) * uy

    def _here(self):
        """Grid cell the bot is in (axle/chassis midpoint)."""
        c, s = math.cos(self.th), math.sin(self.th)
        return cell_of(self.x + 0.016 * c, self.y + 0.016 * s)

    # ------------------------------------------------------------- planning
    def _plan(self, start_cell, start_h, prepend=None):
        pellets = [p for p in self.pellets if p not in self.skip_pellets]
        states = None
        order, cost = None, 0.0
        for danger, use_blocked in ((self.danger, True), (set(), True), (set(), False)):
            saved = self.view.blocked
            if not use_blocked:
                self.view.blocked = set()
            states, order, cost = plan_route(self.view, start_cell, start_h, pellets, danger)
            self.view.blocked = saved
            if states is not None:
                break
            self.log("no route, relaxing constraints")
        self.gate = None
        self.ep_end = None
        self.commit = False
        if states is None:
            self.log("NO ROUTE FOUND - check the maze API")
            self.actions = []
            return
        if prepend:
            states = prepend + states
        self.actions = build_actions(states, self.danger)
        self.idx = 0
        self.log(f"order {order}, plan cost {cost:.1f}s, {len(self.actions)} actions")
        for a in self.actions:
            if a[0] == "gate":
                self.log("   gate (wait for the ghost)")
            elif a[0] == "turn":
                self.log(f"   turn -> {'ENWS'[a[1]]}")
            else:
                self.log(f"   {a[0]} {a[2]} -> {a[1]}")

    def _replan_here(self, why):
        cell = self._here()
        if not self.view.in_bounds(cell):
            cell = (min(max(cell[0], 0), N - 1), min(max(cell[1], 0), N - 1))
        h = quadrant(self.th)
        self.cell, self.dir = cell, h
        self.log(f"re-planning from {cell} facing {'ENWS'[h]} ({why})")
        self._plan(cell, h)
        if abs(wrap(h * math.pi / 2 - self.th)) > TURN_TOL:     # square up first
            self.actions.insert(0, ("turn", h, 0))
        if cell in self.danger and self.model.valid:
            # starting inside the ghost's cells: the plan has no gate for this part
            self.ep_end = self._episode_end(0)
            self.mode = "next"
            if self._episode_clear(self.actions[:self.ep_end + 1]) < RECHECK_CLEAR[0]:
                self._escape("re-planned inside the ghost's cells")
            return
        self.mode = "next"

    # ------------------------------------------------------------- ghost timing
    def _virtual(self, actions, x=None, y=None, th=None, limit=14.0):
        """Simulate our own controller on `actions`: axle poses every VDT seconds."""
        if x is None:
            x, y, th = self.x, self.y, self.th
        pts = []
        n_max = int(limit / VDT)
        for a in actions:
            if len(pts) > n_max:
                break
            k = a[0]
            if k in ("run", "exit_run", "back"):
                d = a[3]
                ux, uy = unit(d)
                sign = -1 if k == "back" else 1
                cx, cy = cell_centre(a[1])
                th = d * math.pi / 2
                rem = sign * ((cx - x) * ux + (cy - y) * uy)
                vmax = V_EXIT if k == "exit_run" else (BACK_V if k == "back" else V_MAX)
                v = 0.0
                while len(pts) <= n_max:
                    if k == "exit_run":
                        if x > EXIT_X:
                            break
                        v = min(vmax, v + ACCEL * VDT)
                    else:
                        if rem <= STOP_TOL:
                            break
                        v = max(min(vmax, math.sqrt(2 * DECEL * rem), v + ACCEL * VDT), V_MIN)
                        rem -= v * VDT
                    x += sign * v * VDT * ux
                    y += sign * v * VDT * uy
                    pts.append((x, y, th))
                if k == "exit_run":
                    break
                pts.extend([(x, y, th)] * int(PAUSE / VDT))
            elif k == "turn":
                pts.extend([(x, y, th)] * int(0.1 / VDT))       # align
                goal = a[1] * math.pi / 2
                for _ in range(300):
                    err = wrap(goal - th)
                    if abs(err) < TURN_TOL:
                        break
                    w = max(min(W_TURN, math.sqrt(2 * A_TURN * abs(err)) * 0.85), W_TURN_MIN)
                    th = wrap(th + math.copysign(min(w * VDT, abs(err)), err))
                    pts.append((x, y, th))
                th = goal
                pts.extend([(x, y, th)] * int(PAUSE / VDT))
            elif k == "hold":
                pts.extend([(x, y, th)] * int(a[1] / VDT))
            else:
                break
        return pts

    def _track(self, t0, dur):
        """Predicted ghost positions from t0 for dur seconds, every VDT."""
        return [self.model.predict(t0 + i * VDT) for i in range(int(dur / VDT) + 2)]

    @staticmethod
    def _cp(pts):
        """Chassis positions (every 2nd virtual step) with their step index."""
        return [(ax + AXLE_BACK * math.cos(th), ay + AXLE_BACK * math.sin(th), i)
                for i, (ax, ay, th) in enumerate(pts) if i % 2 == 0]

    def _clear(self, cp, start, track, t0, need, stretches=STRETCH):
        """Smallest chassis-ghost distance if we start the chassis path `cp` at time
        `start`. Stops early (returning the offending distance) as soon as a requirement
        in `need` fails."""
        worst = 9.0
        n = len(track)
        base = (start - t0) / VDT
        # the bot can be slower than planned (learned scale x stretch) or as fast as the
        # unscaled model (straights are often faster than the average learned scale)
        factors = [(k * self.tl_scale, need[0] if k == 1.0 else need[1]) for k in stretches]
        if len(stretches) > 1 and self.tl_scale > 1.0:
            factors.append((0.95, need[1]))
        for f, lim in factors:
            for cx, cy, i in cp:
                j = int(base + f * i + 0.5)
                if j < n:
                    gx, gy = track[j]
                else:
                    gx, gy = self.model.predict(t0 + j * VDT)
                d = math.hypot(cx - gx, cy - gy)
                if d < worst:
                    worst = d
                    if d < lim:
                        return d
        return worst

    def _episode_end(self, i):
        """Index of the action that takes us out of the ghost's cells (from action i)."""
        for j in range(i, len(self.actions)):
            a = self.actions[j]
            if a[0] == "exit_run" or (a[0] in ("run", "back") and a[1] not in self.danger):
                return j
        return len(self.actions) - 1

    def _episode_clear(self, actions):
        pts = self._virtual(actions)
        if not pts:
            return 9.0
        track = self._track(self.clock, len(pts) * VDT * self.tl_scale * 1.3 + 0.5)
        return self._clear(self._cp(pts), self.clock, track, self.clock, (0.0, 0.0), (1.0, 1.2))

    def _find_window(self, actions, pose=None):
        """Earliest start time at which `actions` stay clear of the ghost. `pose` is the
        axle pose they start from (default: where we are)."""
        x, y, th = pose if pose else (self.x, self.y, self.th)
        pts = self._virtual(actions, x, y, th)
        horizon = (self.model.period or 10.0) + 3.0
        span = len(pts) * VDT * self.tl_scale * max(STRETCH) + 0.5
        t0 = self.clock
        track = self._track(t0, horizon + span)
        bx, by = x + AXLE_BACK * math.cos(th), y + AXLE_BACK * math.sin(th)
        wait_ok = []
        for i in range(len(track)):            # is waiting here safe up to time i?
            gx, gy = track[i]
            ok = math.hypot(gx - bx, gy - by) >= WAIT_CLEAR
            wait_ok.append(ok and (not wait_ok or wait_ok[-1]))
        starts = [0.05 * k for k in range(1, int(horizon / 0.05))]
        cp = self._cp(pts)
        for need, stretches in GATE_LEVELS:
            for s in starts:
                if not wait_ok[min(len(wait_ok) - 1, int(s / VDT))]:
                    break
                if self._clear(cp, t0 + s, track, t0, need, stretches) >= need[0]:
                    return t0 + s, (need, stretches)
        # nothing meets even the relaxed clearance: take the best nominal start
        best = max(starts, key=lambda s: self._clear(cp, t0 + s, track, t0, (0.0, 0.0), (1.0,)))
        return t0 + best, None

    def _plan_avoiding(self, drop=False):
        """A crossing of the ghost's cells has no safe window: route around them instead.
        With drop=True (the crossing kept failing), pellets that can only be reached
        through the ghost's cells are given up, as long as the exit stays reachable."""
        if self.avoid_tried and not drop:
            return False
        self.avoid_tried = True
        here, h = self._here(), quadrant(self.th)
        free = {(r, c) for r in range(N) for c in range(N)} - self.danger
        outside = RestrictedView(self.view, free)
        pellets = [p for p in self.pellets if p not in self.skip_pellets]
        if drop:
            seen, todo = {here}, [here]
            while todo:
                c = todo.pop()
                for d in range(4):
                    nb = (c[0] + DELTA[d][0], c[1] + DELTA[d][1])
                    if nb not in seen and outside.can_move(c, d):
                        seen.add(nb)
                        todo.append(nb)
            if (self.view.exit_row, N - 1) not in seen:
                return False                  # the exit itself needs the crossing
            lost = [p for p in pellets if p not in seen]
            if lost:
                self.log(f"crossing keeps failing: giving up {lost}, exiting with the rest")
                self.skip_pellets |= set(lost)
                pellets = [p for p in pellets if p in seen]
        states, order, cost = plan_route(outside, here, h, pellets, set())
        if states is None:
            self.log("no route around the ghost's cells")
            return False
        self.actions = build_actions(states, set())
        self.idx = 0
        self.ep_end = None
        self.gate = None
        self.mode = "next"
        self.log(f"crossing has no safe window: going around the ghost's cells "
                 f"({cost:.1f}s, order {list(order)})")
        return True

    def _plan_in_and_out(self):
        """The gate has no safe window. Try every entrance to the ghost's cells: dash in to
        the pellet(s) inside and straight back out (turning round, or reversing out the
        way we came), and keep the cheapest dash with a clear timing window. If all the
        pellets inside cannot be done in one dash, try fewer of them."""
        targets = [p for p in self.pellets if p in self.danger and p not in self.skip_pellets]
        if not targets or self.inside_tries >= 4:
            return False
        self.inside_tries += 1
        here, h_now = self._here(), quadrant(self.th)
        outside = RestrictedView(self.view, {(r, c) for r in range(N) for c in range(N)}
                                 - self.danger)
        entries = []
        for d in self.danger:
            for h in range(4):
                s = (d[0] - DELTA[h][0], d[1] - DELTA[h][1])
                if (s in self.danger or (s, h) in self.bad_entries or not self.view.in_bounds(s)
                        or not self.view.can_move(s, h)):
                    continue
                outside.allowed.add(s)
                to_s, _ = dijkstra_segment(outside, here, h_now, s, h, set())
                outside.allowed.discard(s)
                if to_s is not None:
                    entries.append((s, h, d, to_s))
        for k in range(len(targets), 0, -1):
            opts = []
            for subset in itertools.combinations(targets, k):
                for s, h, d, to_s in entries:
                    inside = RestrictedView(self.view, set(self.danger) | {s})
                    for perm in itertools.permutations(subset):
                        cur, states = (s, h), [(s, h)]
                        for goal in perm:
                            path, _ = dijkstra_segment(inside, cur[0], cur[1], goal, None, set())
                            if path is None:
                                break
                            states += path[1:]
                            cur = path[-1]
                        else:
                            if len(states) < 2 or states[1][0] != d:
                                continue
                            dashes = [build_actions(states, set()) + reverse_actions(states)]
                            back, _ = dijkstra_segment(inside, cur[0], cur[1], s, None, set())
                            if back is not None:
                                dashes.append(build_actions(states + back[1:], set()))
                            sx, sy = cell_centre(s)
                            for acts in dashes:
                                dur = len(self._virtual(acts, sx, sy, h * math.pi / 2)) * VDT
                                opts.append((len(to_s) * PLAN_CELL + dur, s, h, subset, acts, to_s))
            opts.sort(key=lambda o: o[0])
            for cost, s, h, subset, acts, to_s in opts[:10]:
                sx, sy = cell_centre(s)
                go, level = self._find_window(acts, (sx, sy, h * math.pi / 2))
                if level is None:
                    continue
                end = (s, quadrant(self._virtual(acts, sx, sy, h * math.pi / 2)[-1][2]))
                rest_pellets = [p for p in self.pellets if p not in subset and p not in self.skip_pellets]
                rest, order, _ = plan_route(self.view, end[0], end[1], rest_pellets, self.danger)
                if rest is None:
                    continue
                self.actions = (build_actions(to_s, set()) + [("gate", None)] + acts
                                + build_actions(rest, self.danger))
                self.idx = 0
                self.ep_end = None
                self.gate = None
                self.mode = "next"
                how = "reversing out" if acts[-1][0] == "back" else "turning round"
                self.log(f"in-and-out plan: wait at {s}, dash in to {list(subset)} and back "
                         f"({how}, clearance {level[0][0]:.2f} m), then {list(order)} and exit")
                return True
        self.log("no in-and-out plan with a safe window")
        return False

    def _reactive_clear(self, waited):
        """Fallback without a ghost model: go when the ghost is far and moving away."""
        g = self._ghost_xy()
        if g is None:
            return True
        cells = [a[1] for a in self.actions[self.idx:self.ep_end + 1] if a[0] in ("run", "exit_run")]
        cells = [c for c in cells if self.view.in_bounds(c)] or [self._here()]
        need = 0.40 if waited < 25.0 else 0.30
        fx, fy = cell_centre(cells[0])
        dnow = math.hypot(g[0] - fx, g[1] - fy)
        old = [r for r in self.recent if r[0] <= self.clock - 0.3]
        moving_away = (not old) or dnow > math.hypot(old[-1][1] - fx, old[-1][2] - fy)
        far = all(math.hypot(g[0] - cell_centre(c)[0], g[1] - cell_centre(c)[1]) > need
                  for c in cells)
        return far and (moving_away or dnow > 0.6)

    def _do_gate(self):
        """True when it is time to enter the ghost's cells."""
        if self.gate_t0 is None:
            self.gate_t0 = self.clock
        if self.ep_end is None:
            self.ep_end = self._episode_end(self.idx)
        ep = self.actions[self.idx:self.ep_end + 1]
        if not self.model.valid:
            if self.gate is None:
                self.gate = ("reactive",)
                self.log("gate: reactive mode (no ghost model)")
            return self._reactive_clear(self.clock - self.gate_t0)
        waited = self.clock - self.gate_t0
        if self.gate is None or self.gate[0] != "model":
            go, level = self._find_window(ep)
            here = self._here()
            if waited > 2.5 * (self.model.period or 10.0) + 3.0:
                level = None                  # windows keep vanishing: treat as infeasible
            need = None if level is None else level[0]
            if need is None or self.gate_escapes.get(here, 0) >= 2:
                if self.gate_escapes.get(here, 0) >= 2:      # this entrance really failed
                    nxt = self.actions[self.idx]
                    self.bad_entries.add((here, nxt[3] if nxt[0] == "run" else self.dir))
                targets = [p for p in self.pellets if p in self.danger and p not in self.skip_pellets]
                if not targets and self._plan_avoiding(drop=self.gate_escapes.get(here, 0) >= 2):
                    return False
                if self._plan_in_and_out():
                    return False
                if targets and (need is None or self.gate_escapes.get(here, 0) >= 3):
                    self.log(f"no safe way to {targets} past the ghost: skipping them")
                    self.skip_pellets |= set(targets)
                    self._replan_here("pellets skipped")
                    return False
                # the only way on: take the best moment and do not turn back for small doubts
                self.commit = True
            self.gate = ("model", go, level)
            self.gate_cell = here
            tag = "best effort" if need is None else f"clearance {need[0]:.2f} m"
            self.log(f"gate: entering in {go - self.clock:.2f}s ({tag}), "
                     f"{len(ep)} actions in the ghost's cells")
        _, go, level = self.gate
        if self.clock < go:
            return False
        if level is None:
            return True
        # last check with the same settings the window was found with (ghost may drift)
        need, stretches = level
        pts = self._virtual(ep)
        track = self._track(self.clock, len(pts) * VDT * self.tl_scale * max(stretches) + 0.5)
        if self._clear(self._cp(pts), self.clock, track, self.clock, need, stretches) >= 0.9 * need[0]:
            return True
        self.gate = None
        return False

    def _escape(self, why):
        """Choose the safest way out right now: carry on, back up, run to a safe cell,
        or stay put, by simulating each against the predicted ghost. Returns True if the
        plan was replaced."""
        self.escape_t = self.clock
        if not self.model.valid:
            self.log(f"ghost close ({why}), no model: holding")
            self.actions = [("hold", 0.5), ("replan",)]
            self.idx = 0
            self.ep_end = None
            self.v = 0.0
            self.mode = "next"
            return True
        here = self._here()
        h = quadrant(self.th)
        cands = []
        if self.ep_end is not None and self.idx - 1 <= self.ep_end and self.act is not None:
            cur = max(0, self.idx - 1)
            cands.append(("carry on", None, self.actions[cur:self.ep_end + 1]))
        back = (h + 2) % 4
        cell = here
        for k in range(1, 4):                    # reverse straight back k cells
            if not self.view.can_move(cell, back):
                break
            cell = (cell[0] + DELTA[back][0], cell[1] + DELTA[back][1])
            cands.append((f"back {k}", cell, [("back", cell, k, h), ("replan",)]))
        seen = {here: 0}
        todo = [here]
        while todo:                              # safe cells within ESCAPE_DEPTH
            c = todo.pop(0)
            if seen[c] >= ESCAPE_DEPTH:
                continue
            for d in range(4):
                if self.view.can_move(c, d):
                    nb = (c[0] + DELTA[d][0], c[1] + DELTA[d][1])
                    if nb not in seen:
                        seen[nb] = seen[c] + 1
                        todo.append(nb)
        safe = sorted((d, c) for c, d in seen.items() if c not in self.danger and c != here)
        for _, c in safe[:12]:
            path, _ = dijkstra_segment(self.view, here, h, c, None, set())
            if path:
                acts = [a for a in build_actions(path, set()) if a[0] != "gate"]
                cands.append((f"to {c}", c, acts + [("replan",)]))
        cands.append(("stay", here, [("hold", 1.0), ("replan",)]))
        horizon = 8.0
        track = self._track(self.clock, horizon * 1.4)
        best = None
        for name, goal, acts in cands:
            pts = self._virtual(acts, limit=horizon)
            if not pts:
                continue
            if goal is not None:               # then wait there for a while
                pts += [pts[-1]] * int(2.0 / VDT)
            score = self._clear(self._cp(pts), self.clock, track, self.clock, (0.0, 0.0), (1.0, 1.25))
            key = (min(score, 0.25), goal not in self.danger, -len(pts))
            if best is None or key > best[0]:
                best = (key, name, acts)
        _, name, acts = best
        self.escapes += 1
        if name != "carry on" and self.gate_cell is not None:
            self.gate_escapes[self.gate_cell] = self.gate_escapes.get(self.gate_cell, 0) + 1
        self.log(f"ESCAPE ({why}): {name}, clearance {best[0][0]:.2f} m")
        if name == "carry on":
            return False
        self.actions = acts
        self.idx = 0
        self.ep_end = None
        self.gate = None
        self.v = 0.0
        self.mode = "next"
        return True

    def _ghost_xy(self):
        if self.ghost is None:
            return None
        x, y = self.ghost.get("x"), self.ghost.get("y")
        if x is None or y is None:
            return None
        return x, y

    def _ghost_close(self):
        """Ghost near the chassis and getting nearer."""
        g = self._ghost_xy()
        if g is None or len(self.recent) < 12:
            return False
        bx, by = self.chassis()
        d = math.hypot(g[0] - bx, g[1] - by)
        if d > THREAT_R:
            return False
        return len(self.gap_hist) > 10 and self.gap_hist[0] > d + 0.002   # gap shrinking

    def _plan_still_clear(self):
        """The ghost is near: does the model say the current plan still passes it safely?"""
        if not self.model.valid or not self.gap_hist or self.gap_hist[-1] < COMMIT_CLEAR + 0.015:
            return False
        acts = self.actions[max(0, self.idx - 1):self.idx + 4]
        pts = self._virtual(acts, limit=3.0)
        if not pts:
            return False
        track = self._track(self.clock, 3.0 * 1.3 * max(1.0, self.tl_scale) + 0.5)
        clear = self._clear(self._cp(pts), self.clock, track, self.clock, (0.0, 0.0), (1.0, 1.15))
        self.escape_t = self.clock          # do not re-check every frame
        return clear >= COMMIT_CLEAR + 0.015

    # ------------------------------------------------------------- main step
    def decide(self, s):
        dt = s.get("dt", 0.01) or 0.01
        self.clock += dt
        if self.done:
            return 0.0, 0.0

        g = self._ghost_xy()
        if g is not None and self.ghost_new:
            self.ghost_new = False
            cell = None
            if "row" in self.ghost and "col" in self.ghost:
                cell = (int(self.ghost["row"]), int(self.ghost["col"]))
            self.model.add(self.clock, g[0], g[1], cell)
            self.recent.append((self.clock, g[0], g[1]))
            if len(self.recent) > 100:
                self.recent.pop(0)

        ticks = (s["ticks_l"], s["ticks_r"])
        if self.prev_ticks is None:
            self.prev_ticks = ticks
            return 0.0, 0.0
        d_left = (ticks[0] - self.prev_ticks[0]) * M_PER_TICK
        d_right = (ticks[1] - self.prev_ticks[1]) * M_PER_TICK
        self.prev_ticks = ticks

        if self.mode == "observe":
            self.gyro_sum += s["gyro"][2]
            self.gyro_n += 1
            return self._observe()

        turn_rate = s["gyro"][2] - self.gyro_bias
        self._odometry(d_left, d_right, turn_rate, dt)

        if not self.model.valid and self.clock - self.last_fit > 2.0 and self.model.samples:
            self.last_fit = self.clock
            if self.model.try_fit():
                self.log(f"ghost loop learned while driving, period {self.model.period:.2f}s")
                self.danger |= self.model.danger_cells()
        if self.model.valid and g is not None:
            px, py = self.model.predict(self.clock)
            if math.hypot(px - g[0], py - g[1]) > MODEL_TOL:
                self.bad_pred += 1
                if self.bad_pred > 30:
                    if self.model.resync(self.recent):
                        self.log("ghost model resynced")
                    else:
                        self.model.valid = False
                        self.log("ghost model lost, falling back to reactive gates")
                    self.bad_pred = 0
            else:
                self.bad_pred = 0

        if g is not None:                   # bot-ghost distance history (for "closing in")
            bx, by = self.chassis()
            self.gap_hist.append(math.hypot(g[0] - bx, g[1] - by))
            if len(self.gap_hist) > 12:
                self.gap_hist.pop(0)

        if self.mode == "run":
            self._correct_sides(s)
            if not self.exiting:
                self._correct_front(s)

        # stall watchdog: wheels commanded but not turning -> pushing on a wall
        if self.mode in ("run", "back", "turn", "align", "c_turn", "c_drive", "nudge"):
            want = (abs(self.cmd[0]) + abs(self.cmd[1])) * WHEEL_RADIUS * dt
            got = abs(d_left) + abs(d_right)
            if want > 4.0 * WHEEL_RADIUS * dt and got < 0.25 * want:
                self.stall_t += dt
            else:
                self.stall_t = max(0.0, self.stall_t - 2 * dt)
            if self.stall_t > STALL_TIME:
                self.stall_t = 0.0
                self._recover("wheels stalled")
        else:
            self.stall_t = 0.0

        # ghost safety net: it should never get this close if the gate timing holds
        if (self.mode in ("run", "align", "turn", "pause", "hold", "back")
                and not (self.mode == "run" and self.exiting and self.chassis()[0] > 3.0)
                and self.clock - self.escape_t > 0.6 and self._ghost_close()
                and not self._plan_still_clear()):
            self.mode = "escape"          # stop this frame, decide on the next one
            self.escape_t = self.clock
            self.v = 0.0
            self.cmd = (0.0, 0.0)
            return self.cmd
        if self.mode == "escape":
            self.mode = "next"
            if not self._escape("ghost closing in"):
                self.idx = max(0, self.idx - 1)     # carry on: restart the current action

        self.cmd = self._drive(s, turn_rate, dt)
        return self.cmd

    def _observe(self):
        if not self.have_pellets:
            return 0.0, 0.0
        if self.obs_t0 is None:
            self.obs_t0 = self.clock
            self.obs_wall0 = time.monotonic()
            self.log("observing ghost (run clock not started yet)")
        el = self.clock - self.obs_t0
        el_wall = time.monotonic() - self.obs_wall0
        if el < OBSERVE_MIN and el_wall < OBSERVE_MAX_WALL:
            return 0.0, 0.0
        go = False
        if el_wall >= OBSERVE_MAX_WALL or el >= OBSERVE_MAX:
            self.log("observation deadline reached")
            go = True
        elif self.ghost is None:
            if el_wall >= GHOST_STATIC_WALL:
                self.log("no ghost data, planning without it")
                go = True
        elif el_wall >= GHOST_STATIC_WALL and self.model.travel() < 0.05:
            self.log("ghost is not moving before the start, using reactive gates")
            go = True
        elif not self.model.valid:
            if el - self.last_fit > 1.0:
                self.last_fit = el
                if self.model.try_fit():
                    self.log(f"ghost loop period {self.model.period:.2f}s")
                    go = True
        else:
            go = True
        if not go:
            return 0.0, 0.0

        self.gyro_bias = self.gyro_sum / max(1, self.gyro_n)
        self.danger = self.model.danger_cells() if self.model.samples else set()
        cen = self.model.centroid()
        if cen and self.pellets:
            self.guarded_pellet = min(
                self.pellets,
                key=lambda p: math.hypot(cell_centre(p)[0] - cen[0], cell_centre(p)[1] - cen[1]))
        self.log(f"gyro bias {self.gyro_bias:.5f}, guarded pellet {self.guarded_pellet}, "
                 f"danger cells {sorted(self.danger)}")
        self._plan((N - 1, 0), 0, prepend=[((N - 1, -1), 0)])
        self.mode = "next"
        self.last_fit = self.clock
        return 0.0, 0.0

    # ------------------------------------------------------------- recovery
    def _recover(self, why):
        """Back off, re-localise, re-centre, re-plan. Never carry on blindly."""
        self.recoveries += 1
        here = self._here()
        key = (here, self.dir)
        if self.mode == "run" and self.view.in_bounds(here):
            self.edge_trouble[key] = self.edge_trouble.get(key, 0) + 1
            if self.edge_trouble[key] >= BLOCK_AFTER and self.view.can_move(here, self.dir):
                self.view.block(here, self.dir)
                self.log(f"edge {here} -> {'ENWS'[self.dir]} keeps failing, routing around it")
        self.log(f"RECOVERY #{self.recoveries}: {why} near {here}")
        self.back_sign = -1 if self.cmd[0] + self.cmd[1] >= 0 else 1
        self.back_left = BACKOFF_DIST
        self.v = 0.0
        self.mode = "backoff"
        self.gate = None

    def _after_settle(self, s):
        """Called once the bot is stopped after a recovery."""
        n = self.cell_recoveries.get(self._here(), 0) + 1
        self.cell_recoveries[self._here()] = n
        if n >= 2:      # repeated trouble here: the pose may be a whole cell off
            self._relocalise(s, 0.30, 0.03, headings=(-0.1, -0.05, 0.0, 0.05, 0.1))
        else:
            self._relocalise(s, 0.25, 0.025, headings=(-0.06, 0.0, 0.06))
        here = self._here()
        if self.view.in_bounds(here):
            cx, cy = cell_centre(here)
            dx, dy = cx - self.x, cy - self.y
            if math.hypot(dx, dy) > 0.02:
                self.centre_goal = (cx, cy)
                self.turn_goal = math.atan2(dy, dx)
                if abs(wrap(self.turn_goal - self.th)) > math.pi / 2:   # back in instead
                    self.turn_goal = wrap(self.turn_goal + math.pi)
                self.mode, self.mode_t0 = "c_turn", self.clock
                self.log(f"re-centring on {here} ({math.hypot(dx, dy):.3f} m off)")
                return
        self._replan_here("after recovery")

    # ------------------------------------------------------------- actions
    def _learn_timing(self):
        if self.act_pred and self.act_pred > 0.3:
            ratio = (self.clock - self.act_t0) / self.act_pred
            self.tl_scale = clamp(0.8 * self.tl_scale + 0.2 * ratio, 0.8, 2.0)

    def _next_action(self):
        if self.idx >= len(self.actions):
            self._replan_here("plan finished inside the maze")
            if not self.actions:
                self.mode, self.wait = "hold", 1.0
            return
        self.act = self.actions[self.idx]
        self.idx += 1
        self.act_t0 = self.clock
        self.act_pred = None
        kind = self.act[0]
        if kind == "replan":
            self._replan_here("escape done")
            return
        if kind == "hold":
            self.mode, self.wait = "pause", self.act[1]
            return
        if kind == "exit_run":
            ex = self.act[1][0]                   # pellets on the exit straight get collected on the way
            todo = [p for p in self.pellets if p not in self.skip_pellets
                    and not (p[0] == ex and p[1] > self._here()[1] and self._here()[0] == ex)]
            if todo:
                if self.pellet_wait < 0.3:          # pellet message may still be on its way
                    self.idx -= 1
                    self.pellet_wait += 0.06
                    self.mode, self.wait = "pause", 0.06
                    return
                self.pellet_wait = 0.0
                if self._here() in todo:
                    self._start_nudge(self._here())
                    return
                self._replan_here(f"pellets left {todo}")
                return
        # inside a ghost episode: is the rest of it still clear?
        if (kind in ("run", "turn", "back") and self.ep_end is not None and self.idx - 1 <= self.ep_end
                and self.model.valid and self.idx - 1 > 0
                and self.actions[self.idx - 2][0] != "gate"):
            clear = self._episode_clear(self.actions[self.idx - 1:self.ep_end + 1])
            if clear < (COMMIT_CLEAR if self.commit else RECHECK_CLEAR[0]):
                if self._escape(f"timing slipped, clearance would be {clear:.2f} m"):
                    return
        if kind in ("run", "exit_run", "back", "turn"):
            self.act_pred = len(self._virtual([self.act])) * VDT
        if self.ep_end is not None and self.idx - 1 > self.ep_end:
            self.ep_end = None
        if kind in ("run", "exit_run"):
            self.dir = self.act[3]
            self.target = self.act[1]
            self.exiting = kind == "exit_run"
            self.mode = "run"
            self.mode_t0 = self.clock
            self.pellet_wait = 0.0
            self.big_front.clear()
            self.big_side.clear()
            if self.t_move is None:
                self.t_move = self.clock
        elif kind == "back":
            self.dir = self.act[3]
            self.target = self.act[1]
            self.mode = "back"
            self.mode_t0 = self.clock
        elif kind == "turn":
            self.dir = self.act[1]
            self.mode = "align"
            self.mode_t0 = self.clock
            if USE_STOP_FIX and self.view.in_bounds(self.cell):
                self.pending_fix = True
        else:
            self.mode = "gate"
            self.gate = None
            self.gate_t0 = None
            self.ep_end = None

    def _start_nudge(self, cell):
        n = self.pellet_tries.get(cell, 0) + 1
        self.pellet_tries[cell] = n
        if n > 3:
            self.log(f"pellet {cell} will not register, skipping it")
            self.skip_pellets.add(cell)
            self._replan_here("pellet skipped")
            return
        # chassis centre onto the cell centre: move the axle AXLE_BACK behind it
        cx, cy = cell_centre(cell)
        c, s = math.cos(self.th), math.sin(self.th)
        self.centre_goal = (cx - AXLE_BACK * c, cy - AXLE_BACK * s)
        self.mode, self.mode_t0 = "nudge", self.clock
        self.log(f"nudging onto pellet {cell} (try {n})")

    def _drive(self, s, turn_rate, dt):
        if self.mode == "pause":
            self.wait -= dt
            if self.wait <= 0:
                self.mode = "next"
            return 0.0, 0.0

        if self.mode == "hold":
            self.wait -= dt
            if self.wait <= 0:
                self._replan_here("hold over")
            return 0.0, 0.0

        if self.mode == "next":
            self._next_action()
            if self.mode in ("pause", "hold", "next"):
                return 0.0, 0.0

        if self.mode == "gate":
            if not self._do_gate():
                return 0.0, 0.0
            self.log(f"gate open, entering {self.actions[self.idx][1]}")
            self.gate = None
            self.gate_t0 = None
            self._next_action()
            if self.mode in ("pause", "hold", "next"):
                return 0.0, 0.0

        if self.mode == "run" and self.exiting:
            if self.chassis()[0] > EXIT_X + 0.03:
                self.log("crossed exit line")
                self.mode = "out"
                return wheels(V_EXIT, 0.0)
            if min(s["sl"], s["sr"]) < FRONT_GUARD:
                self._recover("wall ahead on the exit run")
                return 0.0, 0.0
            if self.clock - self.mode_t0 > 4.0 + 2.5 * self.act[2] * PLAN_CELL:
                self._recover("exit run timed out")
                return 0.0, 0.0
            self.v = max(min(V_EXIT, self.v + ACCEL * dt), V_MIN)
            return wheels(self.v, self._steer(self.target, s))

        if self.mode == "run":
            return self._run(s, dt)

        if self.mode == "back":
            ux, uy = unit(self.dir)
            cx, cy = cell_centre(self.target)
            rem = (self.x - cx) * ux + (self.y - cy) * uy
            if rem <= STOP_TOL or self.clock - self.mode_t0 > 2.0 + 2.0 * self.act[2]:
                self._learn_timing()
                self.cell = self.target
                self.v = 0.0
                self.mode, self.wait = "pause", PAUSE
                return 0.0, 0.0
            self.v = max(min(BACK_V, math.sqrt(2 * DECEL * rem), self.v + ACCEL * dt), V_MIN)
            return wheels(-self.v, self._hold_heading())

        if self.mode == "align":
            if self.pending_fix:
                self.pending_fix = False
                self._relocalise(s, 0.04, 0.01, log=False)
            ux, uy = math.cos(self.th), math.sin(self.th)
            cx, cy = cell_centre(self.cell)
            rem = (cx - self.x) * ux + (cy - self.y) * uy
            face_dir = quadrant(self.th)
            sl, sr = s["sl"], s["sr"]
            if (self.view.wall(self.cell, face_dir) and abs(sl - sr) < 0.02
                    and max(sl, sr) < 0.25):
                rem = 0.5 * (sl + sr) - FRONT_AT_CENTRE     # drive until the reading is right
            if abs(rem) <= ALIGN_TOL or self.clock - self.mode_t0 > ALIGN_TIMEOUT:
                self.mode = "turn"
                self.mode_t0 = self.clock
                return 0.0, 0.0
            v = clamp(3.0 * rem, -0.10, 0.10)
            if abs(v) < 0.02:
                v = math.copysign(0.02, v)
            return wheels(v, 0.0)

        if self.mode == "turn":
            err = wrap(self.dir * math.pi / 2 - self.th)
            out = self._pivot(err, turn_rate)
            if out is None:
                self._learn_timing()
                self.mode, self.wait = "pause", PAUSE
                return 0.0, 0.0
            if self.clock - self.mode_t0 > TURN_TIMEOUT:
                self._recover(f"turn timed out (err {err:+.3f})")
                return 0.0, 0.0
            return out

        if self.mode == "backoff":
            self.back_left -= BACKOFF_V * dt
            if self.back_left <= 0:
                self.mode, self.wait = "settle", 0.15
                return 0.0, 0.0
            return wheels(self.back_sign * BACKOFF_V, 0.0)

        if self.mode == "settle":
            self.wait -= dt
            if self.wait <= 0:
                self._after_settle(s)
            return 0.0, 0.0

        if self.mode == "c_turn":
            err = wrap(self.turn_goal - self.th)
            out = self._pivot(err, turn_rate)
            if out is None:
                self.mode, self.mode_t0 = "c_drive", self.clock
                return 0.0, 0.0
            if self.clock - self.mode_t0 > TURN_TIMEOUT:
                self._replan_here("re-centre turn failed")
                return 0.0, 0.0
            return out

        if self.mode in ("c_drive", "nudge"):
            ux, uy = math.cos(self.th), math.sin(self.th)
            rem = (self.centre_goal[0] - self.x) * ux + (self.centre_goal[1] - self.y) * uy
            if abs(rem) <= ALIGN_TOL or self.clock - self.mode_t0 > 2.5:
                if self.mode == "nudge":
                    self.mode, self.wait = "pause", 0.3
                    self.idx = max(0, self.idx - 1)     # retry the exit run (re-checks pellets)
                else:
                    self._replan_here("re-centred")
                return 0.0, 0.0
            v = clamp(3.0 * rem, -0.10, 0.10)
            if abs(v) < 0.02:
                v = math.copysign(0.02, v)
            return wheels(v, 0.0)

        if self.mode == "out":
            return wheels(V_EXIT, clamp(-K_HEAD * wrap(self.th), -W_DRIVE, W_DRIVE))

        return 0.0, 0.0

    def _pivot(self, err, turn_rate):
        """Rate-profiled pivot. Returns wheel speeds, or None when the turn is done."""
        if abs(err) < TURN_TOL and abs(turn_rate) < TURN_RATE_DONE:
            return None
        w = min(W_TURN, math.sqrt(2 * A_TURN * abs(err)) * 0.85)
        w = max(w, W_TURN_MIN)
        if abs(err) < TURN_TOL:
            w = 0.0
        return wheels(0.0, math.copysign(w, err))

    def _run(self, s, dt):
        rem = self._remaining(self.target)
        sl, sr = s["sl"], s["sr"]
        front = min(sl, sr)

        # wall ahead of the target cell: stop on the wall reading (absolute)
        if (rem < 0.10 and self.view.wall(self.target, self.dir)
                and abs(sl - sr) < 0.02 and max(sl, sr) <= FRONT_AT_CENTRE):
            ux, uy = unit(self.dir)
            cx, cy = cell_centre(self.target)
            along = (self.x - cx) * ux + (self.y - cy) * uy
            self.x -= along * ux
            self.y -= along * uy
            rem = 0.0

        if rem <= STOP_TOL:
            return self._arrive()

        # something right in front of the bumper
        if front < FRONT_GUARD:
            if rem < 0.03:
                return self._arrive()           # it is the expected wall, we are there
            self._recover(f"obstacle {front:.3f} m ahead, {rem:.3f} m before {self.target}")
            return 0.0, 0.0

        limit = 3.0 + 2.5 * (self.act_pred or self.act[2] * PLAN_CELL)
        if self.clock - self.mode_t0 > limit:
            self._recover(f"run to {self.target} timed out ({rem:+.3f} m left)")
            return 0.0, 0.0

        v_brake = math.sqrt(2 * DECEL * rem)
        v = min(V_MAX, v_brake, self.v + ACCEL * dt)
        if front < FRONT_SLOW:      # one sensor sees a post / wall end close by: crawl
            v = min(v, 0.08)
        v = max(v, V_MIN)
        self.v = v
        return wheels(v, self._steer(self.target, s))

    def _arrive(self):
        self._learn_timing()
        self.cell = self.target
        self.v = 0.0
        self.mode, self.wait = "pause", PAUSE
        return 0.0, 0.0


# ======================================================================= mqtt
def _mqtt_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        return mqtt.Client()


def main():
    maze = Maze.official()
    print(f"official maze {maze_2b.MAZE_ID}: {maze.grid}x{maze.grid}, "
          f"spawn row {maze.spawn_row}, exit row {maze.exit_row}")

    controller = Controller(maze)
    client = _mqtt_client()

    def on_message(cli, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            return

        if msg.topic == TOPIC_PELLETS:
            controller.on_pellets(payload)
            return
        if msg.topic == TOPIC_GHOST:
            controller.on_ghost(payload)
            return
        if msg.topic == TOPIC_RESULT:
            print(f"run over: solved={payload['solved']}, collisions={payload['collisions']}", flush=True)
            controller.done = True
            cli.disconnect()
            return
        if msg.topic == TOPIC_SENSORS:
            left, right = controller.decide(payload)
            left = max(-WHEEL_LIMIT, min(WHEEL_LIMIT, float(left)))
            right = max(-WHEEL_LIMIT, min(WHEEL_LIMIT, float(right)))
            cli.publish(
                TOPIC_WHEEL_VEL,
                json.dumps({"left": left, "right": right}),
            )

    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe(TOPIC_PELLETS)
    client.subscribe(TOPIC_GHOST)
    client.subscribe(TOPIC_RESULT)
    client.subscribe(TOPIC_SENSORS)
    print("connected; waiting for the simulator", flush=True)
    client.loop_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
