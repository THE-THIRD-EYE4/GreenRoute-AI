"""Offline test bench for the Task 2B controller (no MuJoCo, no MQTT).

A simple 2-D stand-in for the official simulator: differential drive with wheel lag
and slip, encoder ticks, a gyro with bias and noise, four ToF rays, wall contacts
(each new contact counts once, like the evaluator), a ghost patrolling a 5-cell
path around one pellet, and the official scoring formula.

It is NOT the real physics; it exists to catch logic problems (hangs, repeated
collisions, bad re-planning, ghost catches) over many random runs.

    python3 pacsim.py                 # 20 random runs
    python3 pacsim.py --runs 50 --seed 3 --verbose
"""
import argparse
import contextlib
import io
import math
import os
import random
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                          # test maze_2b stand-in
sys.path.insert(1, os.path.dirname(HERE))         # the controller

# the controller imports paho at module level; the bench does not need it
_paho = types.ModuleType("paho")
_mqtt = types.ModuleType("paho.mqtt")
_client = types.ModuleType("paho.mqtt.client")
_paho.mqtt = _mqtt
_mqtt.client = _client
sys.modules.setdefault("paho", _paho)
sys.modules.setdefault("paho.mqtt", _mqtt)
sys.modules.setdefault("paho.mqtt.client", _client)

import maze_2b  # noqa: E402

CTRL_MODULE = os.environ.get("PACBOT_CTRL", "task_2b_v5")
ctrl = __import__(CTRL_MODULE)

PITCH = 0.22
WALL_HALF = 0.005
R = 0.017
HT = 0.039
AXLE_BACK = 0.033
HALF_LEN, HALF_WID = 0.046, 0.043     # conservative rectangular footprint
GHOST_R = 0.04
GHOST_V = 0.15
DT = 0.01
SUB = 5


class Sim:
    def __init__(self, seed, verbose=False, harsh=False):
        self.rng = random.Random(seed)
        self.verbose = verbose
        k = 2.0 if harsh else 1.0          # --harsh: twice the imperfections
        self.maze = maze_2b.Maze.official()
        with contextlib.redirect_stdout(io.StringIO()):
            self.view = ctrl.MazeView(self.maze)
        rng = self.rng
        # imperfections
        self.k_l = 1.0 + k * rng.uniform(-0.02, 0.02)
        self.k_r = 1.0 + k * rng.uniform(-0.02, 0.02)
        self.gyro_bias = k * rng.uniform(-0.01, 0.01)
        self.sensor_err = {n: (k * rng.uniform(-0.002, 0.002), k * rng.uniform(-0.002, 0.002))
                           for n, _, _ in ctrl.SENSORS}
        self.tau = k * rng.uniform(0.02, 0.05)
        self.slip = 0.01 * k
        # robot
        self.cx, self.cy, self.th = -0.15, 0.0, 0.0
        self.wl = self.wr = 0.0
        self.cmd = (0.0, 0.0)
        self.ang_l = self.ang_r = 0.0
        self.t = 0.0
        self.t_start = None
        self.contact = False
        self.collisions = 0
        self.contact_log = []
        # pellets / ghost
        reach = self._reachable()
        cand = [c for c in reach if c not in ((14, 0), (7, 14), (14, 1))]
        self.pellets = rng.sample(cand, 3)
        self.collected = []
        self.ghost_path = self._ghost_path(self.pellets[rng.randrange(3)])
        self.gs = rng.uniform(0, 1)            # phase
        self.result = None

    # ------------------------------------------------------------- maze helpers
    def _reachable(self):
        seen = {(14, 0)}
        todo = [(14, 0)]
        while todo:
            c = todo.pop()
            for d, (dr, dc) in enumerate(ctrl.DELTA):
                if self.view.can_move(c, d):
                    n = (c[0] + dr, c[1] + dc)
                    if n not in seen:
                        seen.add(n)
                        todo.append(n)
        return sorted(seen)

    def _ghost_path(self, pellet):
        rng = self.rng
        for _ in range(500):
            path = [pellet]
            while len(path) < 5:
                ends = [path[0], path[-1]]
                k = rng.randrange(2)
                end = ends[k]
                opts = []
                for d, (dr, dc) in enumerate(ctrl.DELTA):
                    n = (end[0] + dr, end[1] + dc)
                    if self.view.can_move(end, d) and n not in path:
                        opts.append(n)
                if not opts:
                    if not any(self.view.can_move(e, d) and (e[0] + ctrl.DELTA[d][0], e[1] + ctrl.DELTA[d][1]) not in path
                               for e in ends for d in range(4)):
                        break
                    continue
                n = rng.choice(opts)
                path = [n] + path if k == 0 else path + [n]
            if len(path) == 5 and (14, 0) not in path and (14, 1) not in path:
                return path
        return [pellet]

    def ghost_xy(self):
        pts = [ctrl.cell_centre(c) for c in self.ghost_path]
        if len(pts) == 1:
            return pts[0]
        seg = len(pts) - 1
        L = seg * PITCH
        s = (self.gs * 2 * L + GHOST_V * self.t) % (2 * L)
        if s > L:
            s = 2 * L - s
        i = min(int(s / PITCH), seg - 1)
        a = s / PITCH - i
        return (pts[i][0] + a * (pts[i + 1][0] - pts[i][0]),
                pts[i][1] + a * (pts[i + 1][1] - pts[i][1]))

    def _point_in_wall(self, px, py):
        # vertical boundaries
        bi = round((px + PITCH / 2) / PITCH)
        bx = bi * PITCH - PITCH / 2
        if abs(px - bx) < WALL_HALF:
            for yy in (py - WALL_HALF, py, py + WALL_HALF):
                row = int(round(14 - yy / PITCH))
                if self.view.edge_wall((row, bi - 1), 0):
                    return True
        bj = round((py + PITCH / 2) / PITCH)
        by = bj * PITCH - PITCH / 2
        if abs(py - by) < WALL_HALF:
            for xx in (px - WALL_HALF, px, px + WALL_HALF):
                col = int(round(xx / PITCH))
                if self.view.edge_wall((15 - bj, col), 1):
                    return True
        return False

    def _collides(self, cx, cy, th):
        c, s = math.cos(th), math.sin(th)
        n = 9
        for i in range(n + 1):
            f = -1 + 2 * i / n
            for lx, ly in ((HALF_LEN, f * HALF_WID), (-HALF_LEN, f * HALF_WID),
                           (f * HALF_LEN, HALF_WID), (f * HALF_LEN, -HALF_WID)):
                if self._point_in_wall(cx + lx * c - ly * s, cy + lx * s + ly * c):
                    return True
        return False

    # ------------------------------------------------------------- physics
    def step(self):
        cl, cr = self.cmd
        if self.t_start is None and (abs(cl) > 1e-6 or abs(cr) > 1e-6):
            self.t_start = self.t
        h = DT / SUB
        gyro = 0.0
        hit = False
        for _ in range(SUB):
            self.wl += (cl - self.wl) * h / self.tau
            self.wr += (cr - self.wr) * h / self.tau
            self.ang_l += self.wl * h
            self.ang_r += self.wr * h
            vl = self.wl * R * self.k_l * (1 + self.rng.gauss(0, self.slip))
            vr = self.wr * R * self.k_r * (1 + self.rng.gauss(0, self.slip))
            v = (vl + vr) / 2
            w = (vr - vl) / (2 * HT)
            ax = self.cx - AXLE_BACK * math.cos(self.th)
            ay = self.cy - AXLE_BACK * math.sin(self.th)
            nth = self.th + w * h
            nax = ax + v * math.cos(self.th + w * h / 2) * h
            nay = ay + v * math.sin(self.th + w * h / 2) * h
            moved = False
            for tx, ty, tth in ((nax, nay, nth), (nax, ay, self.th), (ax, nay, self.th)):
                ncx = tx + AXLE_BACK * math.cos(tth)
                ncy = ty + AXLE_BACK * math.sin(tth)
                if not self._collides(ncx, ncy, tth):
                    if (tx, ty, tth) != (nax, nay, nth):
                        hit = True
                    gyro += (tth - self.th) / DT
                    self.cx, self.cy, self.th = ncx, ncy, tth
                    moved = True
                    break
            if not moved:
                hit = True
        if hit and not self.contact:
            self.collisions += 1
            self.contact_log.append((round(self.t, 2), ctrl.cell_of(self.cx, self.cy)))
            if self.verbose:
                print(f"    SIM: collision #{self.collisions} at t={self.t:.2f} "
                      f"cell {ctrl.cell_of(self.cx, self.cy)}")
        self.contact = hit
        self.t += DT
        # pellets
        before = len(self.pellets)
        for p in list(self.pellets):
            px, py = ctrl.cell_centre(p)
            if math.hypot(px - self.cx, py - self.cy) < 0.06:
                self.pellets.remove(p)
                self.collected.append(p)
        changed = len(self.pellets) != before
        # ghost
        gx, gy = self.ghost_xy()
        if math.hypot(gx - self.cx, gy - self.cy) < HALF_WID + GHOST_R:
            self.result = "caught"
        if self.cx > 3.19:
            self.result = "exited"
        return gyro, changed

    def sensors(self, gyro):
        out = {}
        c, s = math.cos(self.th), math.sin(self.th)
        for name, (sx, sy), a in ctrl.SENSORS:
            ex, ey = self.sensor_err[name]
            sx, sy = sx + ex, sy + ey
            d = self.view.raycast(self.cx + sx * c - sy * s, self.cy + sx * s + sy * c, self.th + a)
            out[name] = min(2.0, max(0.0, d + self.rng.gauss(0, 0.0015)))
        out["gyro"] = [0.0, 0.0, gyro + self.gyro_bias + self.rng.gauss(0, 0.01)]
        out["accel"] = [0.0, 0.0, 9.81]
        out["ticks_l"] = int(self.ang_l / (2 * math.pi) * 360)
        out["ticks_r"] = int(self.ang_r / (2 * math.pi) * 360)
        out["dt"] = DT
        out["t"] = 0.0 if self.t_start is None else self.t - self.t_start
        return out

    def run(self, max_t=420.0):
        buf = io.StringIO()
        out = sys.stdout if self.verbose else buf
        with contextlib.redirect_stdout(out):
            c = ctrl.Controller(self.maze)
            c.on_pellets({"pellets": [list(p) for p in self.pellets]})
            gyro = 0.0
            while self.result is None and self.t < max_t:
                gx, gy = self.ghost_xy()
                gr, gc = ctrl.cell_of(gx, gy)
                c.on_ghost({"row": gr, "col": gc, "x": gx, "y": gy})
                self.cmd = c.decide(self.sensors(gyro))
                gyro, changed = self.step()
                if changed:
                    c.on_pellets({"pellets": [list(p) for p in self.pellets]})
        self.log = buf.getvalue()
        P = len(self.collected)
        run_t = None if self.t_start is None else self.t - self.t_start
        exited = self.result == "exited"
        if not exited:
            marks = 0.0
        else:
            T = max(10.0, 55 - 0.085 * run_t) if P == 3 else 0.0
            marks = max(0.0, 5 * P + T - 1.5 * self.collisions)
        return dict(result=self.result or "timeout", pellets=P, collisions=self.collisions,
                    time=run_t, marks=marks, recoveries=c.recoveries)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--harsh", action="store_true", help="twice the slip, lag and sensor errors")
    a = ap.parse_args()
    tot = []
    for k in range(a.runs):
        sim = Sim(a.seed + k, a.verbose, a.harsh)
        r = sim.run()
        tot.append(r)
        t = "-" if r["time"] is None else f"{r['time']:6.1f}s"
        print(f"seed {a.seed + k:3d}: {r['result']:7s} pellets {r['pellets']} "
              f"collisions {r['collisions']} recoveries {r['recoveries']} time {t} "
              f"marks {r['marks']:5.2f}  pellets@{sim.collected + sim.pellets} ghost {sim.ghost_path[0]}..")
        if r["collisions"] and not a.verbose:
            print("      contacts:", sim.contact_log)
    n = len(tot)
    ok = sum(r["result"] == "exited" and r["pellets"] == 3 for r in tot)
    print(f"\n{ok}/{n} full runs, mean marks {sum(r['marks'] for r in tot) / n:.2f}, "
          f"collisions {sum(r['collisions'] for r in tot)}, "
          f"caught {sum(r['result'] == 'caught' for r in tot)}, "
          f"timeouts {sum(r['result'] == 'timeout' for r in tot)}")


if __name__ == "__main__":
    main()
