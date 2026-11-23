# SPDX-FileCopyrightText: 2026 Robin Mariančík
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Open curves: resample a selected edge path that is not a closed loop.

Typical use: select one profile path across a bevel (from the side wall to the
cap) and change its segment count. The path is one *column* of a quad *band*
(here: the bevel all around the cylinder); every column of the band is
resampled together, so the result stays all quads.

Overview
--------
1. ``extract_selected_paths``: the edge selection split into open vertex paths.
2. ``build_band``: from a path, walk sideways through quads to collect the
   parallel columns (closed all the way around, or open with two end columns).
3. Every column gets a ``Curve`` fit:

   * a corner frame from the directions of the geometry the column continues
     into at both ends (the side wall and the cap for a bevel): corner C,
     a = A - C, b = B - C. In it a point is C + x a + y b and the profile is
     the superellipse (1-x)^e + (1-y)^e = 1, the same family Blender's Bevel
     uses (e = 2 is round, e = 1 is a straight chamfer, large e is a sharp
     corner). Its "profile" value p = 0.5^(2/e) matches Bevel's Profile.
   * a Catmull-Rom spline through the original points when the column is not
     such a superellipse.

4. ``resample``: new points along each column (keeping the original spacing,
   or evenly spaced), the band faces between columns, and every other face
   that touches the columns is re-stitched to the new vertices.
"""

import math

import numpy as np
from mathutils import Vector

from .core import (
    FIT_AUTO, FIT_CIRCLE, FIT_SPLINE,
    _Jobs, _face_goes, _get_edge, _round_half_up, _shares_small_face, edge_between,
)

MIN_SEGMENTS = 1
MAX_SEGMENTS = 512

# A column counts as a clean profile when every vertex is within this fraction
# of the chord length from the fitted superellipse.
CURVE_TOLERANCE = 0.01

PROFILE_MIN = 0.02
PROFILE_MAX = 0.98

HALF_PI = 0.5 * math.pi


def profile_to_exp(p):
    p = min(max(p, PROFILE_MIN), PROFILE_MAX)
    return 2.0 * math.log(0.5) / math.log(p)


def exp_to_profile(e):
    return 0.5 ** (2.0 / e)


# -----------------------------------------------------------------------------
# Selection -> open paths


def extract_selected_paths(bm):
    """Ordered vertex lists of the open edge paths in the selection. Branch
    points split paths; closed loops are ignored (those are rings)."""
    sel = [e for e in bm.edges if e.select and not e.hide]
    adj = {}
    for e in sel:
        for v in e.verts:
            adj.setdefault(v, []).append(e)
    used = set()
    paths = []

    ends = []
    seen = set()
    for e in sel:
        for v in e.verts:
            if v not in seen and len(adj[v]) != 2:
                seen.add(v)
                ends.append(v)

    for v0 in ends:
        for e0 in adj[v0]:
            if e0 in used:
                continue
            verts = [v0]
            cur, edge = v0, e0
            while True:
                used.add(edge)
                nxt = edge.other_vert(cur)
                verts.append(nxt)
                if len(adj[nxt]) != 2:
                    break
                e2 = adj[nxt][0] if adj[nxt][1] == edge else adj[nxt][1]
                if e2 in used:
                    break
                cur, edge = nxt, e2
            if len(set(verts)) == len(verts):
                paths.append(verts)
    return paths


# -----------------------------------------------------------------------------
# Path -> band of parallel columns


def _next_column(col, edges, f):
    """The column next to ``col`` across face f (adjacent to edges[0]).
    Returns (verts, faces) or None if that side is not a clean quad band."""
    m = len(edges)
    nxt = [None] * (m + 1)
    faces = []
    cset = set(col)
    for i in range(m):
        e = edges[i]
        if f is None or len(f.verts) != 4:
            return None
        l = None
        for lp in f.loops:
            if lp.edge == e:
                l = lp
                break
        if l is None:
            return None
        if l.vert == col[i]:
            w1 = l.link_loop_next.link_loop_next.vert
            w0 = l.link_loop_prev.vert
            rung = l.link_loop_next.edge
        else:
            w0 = l.link_loop_next.link_loop_next.vert
            w1 = l.link_loop_prev.vert
            rung = l.link_loop_prev.edge
        if w0 in cset or w1 in cset:
            return None
        for idx, w in ((i, w0), (i + 1, w1)):
            if nxt[idx] is None:
                nxt[idx] = w
            elif nxt[idx] != w:
                return None
        faces.append(f)
        if i < m - 1:
            others = [x for x in rung.link_faces if x != f]
            if len(others) != 1:
                return None
            f = others[0]
    if len(set(nxt)) != m + 1 or len(set(faces)) != m:
        return None
    return nxt, faces


class Band:
    def __init__(self):
        self.columns = []    # vertex lists, all m + 1 long
        self.col_edges = []  # col_edges[c][i]: edge between rows i and i + 1
        self.strips = []     # strips[c][i]: face between columns c, c + 1 and rows i, i + 1
        self.closed = False
        self.m = 0
        self.curves = []
        self.mode = FIT_SPLINE
        self.profile = None  # base profile value for the HUD (None: no corner frame)
        self.sel = 0         # index of the column the user selected
        self.collapsed = None  # (merged positions, radii) after collapse()
        # filled by resample()
        self.W = None
        self.M = 0

    def faces(self):
        return set(f for s in self.strips for f in s)

    def has_radius(self):
        """Every column has something to scale about (a corner or a circle)."""
        return bool(self.curves) and all(cv.radius_center() is not None for cv in self.curves)

    def interior(self):
        return set(v for col in self.columns for v in col[1:-1])


def build_band(path):
    col0 = list(path)
    m = len(col0) - 1
    if m < 1:
        return None
    edges0 = [edge_between(col0[i], col0[i + 1]) for i in range(m)]
    if any(e is None for e in edges0):
        return None
    band = Band()
    band.m = m
    band.columns = [col0]
    band.col_edges = [edges0]
    claimed = set(col0)
    used_faces = set()

    sides = list(edges0[0].link_faces)
    if len(sides) > 2:
        sides = []

    def walk(f, forward):
        cur, cur_edges = col0, edges0
        while f is not None:
            res = _next_column(cur, cur_edges, f)
            if res is None:
                return
            nxt, faces = res
            if used_faces.intersection(faces):
                return
            if forward and nxt == col0:
                band.strips.append(faces)
                used_faces.update(faces)
                band.closed = True
                return
            if any(v in claimed for v in nxt):
                return
            nedges = [edge_between(nxt[i], nxt[i + 1]) for i in range(m)]
            if any(e is None for e in nedges):
                return
            claimed.update(nxt)
            used_faces.update(faces)
            if forward:
                band.columns.append(nxt)
                band.col_edges.append(nedges)
                band.strips.append(faces)
            else:
                band.columns.insert(0, nxt)
                band.col_edges.insert(0, nedges)
                band.strips.insert(0, faces)
                band.sel += 1
            fs = set(faces)
            others = [x for x in nedges[0].link_faces if x not in fs]
            f = others[0] if len(others) == 1 else None
            cur, cur_edges = nxt, nedges

    if sides:
        walk(sides[0], True)
    if not band.closed and len(sides) > 1 and sides[1] not in used_faces:
        walk(sides[1], False)
    return band


# -----------------------------------------------------------------------------
# Leg directions (where the geometry continues past the curve's ends)


def _np(v):
    return np.array((v.x, v.y, v.z), dtype=np.float64)


def _unit(d):
    n = np.linalg.norm(d)
    return d / n if n > 1e-15 else None


def _leg_dir(v, inward, band_faces, row_tangent):
    # row_tangent: direction across which legs are flattened (the column's
    # plane normal, or the row direction for single-segment columns).
    """Direction of the surface the curve continues into at end vertex v,
    pointing from that surface through v toward the curve's corner.

    Returns (direction or None, how it was found: 'edge', 'cap' (an n-gon or
    triangle past the end), 'quad' (only plain quads past the end) or None)."""
    e_in = edge_between(v, inward)
    into = _np(inward.co) - _np(v.co)
    # 1. An edge that continues the column (does not share a tri/quad with it).
    best, best_dot = None, 0.0
    for e in v.link_edges:
        if e == e_in or _shares_small_face(e_in, e):
            continue
        d = _np(v.co) - _np(e.other_vert(v).co)
        if row_tangent is not None:
            # Only the part across the band counts: a diagonal of a
            # triangulated cap then acts like a straight outward edge.
            d = d - row_tangent * d.dot(row_tangent)
        d = _unit(d)
        if d is None:
            continue
        dot = float(d.dot(into)) / max(np.linalg.norm(into), 1e-15)
        if dot > best_dot:
            best, best_dot = d, dot
    if best is not None:
        return best, 'edge'
    # 2. A face past the end (a cap): radial direction in it.
    acc = np.zeros(3)
    quads_only = True
    for f in v.link_faces:
        if f in band_faces or e_in in f.edges:
            continue
        quads_only = quads_only and len(f.verts) == 4
        d = _np(v.co) - _np(f.calc_center_median())
        nf = _np(f.normal)
        d = d - nf * d.dot(nf)
        if row_tangent is not None:
            d = d - row_tangent * d.dot(row_tangent)
        u = _unit(d)
        if u is not None:
            acc += u
    d = _unit(acc)
    if d is not None and float(d.dot(into)) > 0.0:
        return d, ('quad' if quads_only else 'cap')
    return None, None


def _end_tangent(P):
    """Tangent at P[0] going into the curve, from the parabola through the
    first three points (chord-length parameters)."""
    h1 = np.linalg.norm(P[1] - P[0])
    h2 = np.linalg.norm(P[2] - P[1])
    if h1 < 1e-15 or h2 < 1e-15:
        return None
    d = (-(2 * h1 + h2) / (h1 * (h1 + h2)) * P[0]
         + (h1 + h2) / (h1 * h2) * P[1]
         - h1 / (h2 * (h1 + h2)) * P[2])
    return _unit(d)


def _corner(A, B, dA, dB):
    """Corner where the legs through A (direction dA) and B (direction dB)
    meet, ahead of both. Returns (C, a, b) or None."""
    if dA is None or dB is None:
        return None
    chord = float(np.linalg.norm(B - A))
    if chord < 1e-12:
        return None
    w = A - B
    aa, bb, ab = dA.dot(dA), dB.dot(dB), dA.dot(dB)
    den = aa * bb - ab * ab
    if den < 1e-4 * aa * bb:  # (nearly) parallel legs
        return None
    d, e = dA.dot(w), dB.dot(w)
    s = (ab * e - bb * d) / den
    t = (aa * e - ab * d) / den
    if s <= 1e-6 * chord or t <= 1e-6 * chord or s > 20 * chord or t > 20 * chord:
        return None
    pa, pb = A + s * dA, B + t * dB
    if np.linalg.norm(pa - pb) > 0.05 * chord:  # skew legs
        return None
    C = 0.5 * (pa + pb)
    return C, A - C, B - C


# -----------------------------------------------------------------------------
# Curve fit of one column


def _solve_exp(u, v):
    """Per point, the exponent e with u^e + v^e = 1 (u, v in (0, 1))."""
    lu, lv = np.log(u), np.log(v)
    lo = np.full(u.shape, math.log(0.05))
    hi = np.full(u.shape, math.log(500.0))
    for _ in range(42):
        mid = 0.5 * (lo + hi)
        e = np.exp(mid)
        g = np.exp(e * lu) + np.exp(e * lv) - 1.0
        up = g > 0.0
        lo = np.where(up, mid, lo)
        hi = np.where(up, hi, mid)
    return np.exp(0.5 * (lo + hi))


def _polar_uv(theta, e):
    """Point of u^e + v^e = 1 in direction theta (polar form: well sampled for
    every exponent, from concave through round to nearly square)."""
    c = np.clip(np.cos(theta), 0.0, 1.0)
    s = np.clip(np.sin(theta), 0.0, 1.0)
    rho = (c ** e + s ** e) ** (-1.0 / e)
    return rho * c, rho * s


def _catmull(P, s):
    """Uniform Catmull-Rom through P (end tangents by reflection), s in [0, 1]."""
    m = len(P) - 1
    ext = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    x = np.clip(np.asarray(s, dtype=np.float64) * m, 0.0, m)
    j = np.minimum(np.floor(x).astype(int), m - 1)
    t = (x - j)[:, None]
    p0, p1, p2, p3 = ext[j], ext[j + 1], ext[j + 2], ext[j + 3]
    t2 = t * t
    t3 = t2 * t
    return 0.5 * ((2.0 * p1)
                  + (p2 - p0) * t
                  + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                  + (3.0 * p1 - p0 - 3.0 * p2 + p3) * t3)


def _frame_coords(frame, Q):
    """Affine coordinates of points Q in a corner frame: Q = C + x a + y b + res."""
    C, a, b = frame
    G = np.array([[a.dot(a), a.dot(b)], [a.dot(b), b.dot(b)]])
    R = Q - C
    xy = np.linalg.solve(G, np.stack([R @ a, R @ b]))
    x, y = xy[0], xy[1]
    return x, y, R - np.outer(x, a) - np.outer(y, b)


def _frame_points(frame, x, y, res=None):
    C, a, b = frame
    Q = C + np.outer(x, a) + np.outer(y, b)
    return Q if res is None else Q + res


def _tighten(frame, Q, e0, e1):
    """Move points from the e0 profile to the e1 profile of a corner frame,
    keeping their direction from the profile center and their relative offset."""
    if e1 == e0:
        return Q
    x, y, res = _frame_coords(frame, Q)
    u = np.clip(1.0 - x, 0.0, None)
    v = np.clip(1.0 - y, 0.0, None)
    lam = (u ** e0 + v ** e0) ** (1.0 / e0)
    u2, v2 = _polar_uv(np.arctan2(v, u), e1)
    return _frame_points(frame, 1.0 - lam * u2, 1.0 - lam * v2, res)


def _shift_exp(e0, shift):
    return profile_to_exp(exp_to_profile(e0) + shift) if shift else e0


def _fit_circle(P):
    """Least-squares circle of the points: (center, normal, radius, max_dev)
    or None for (nearly) straight point sets."""
    if len(P) < 3:
        return None
    c = P.mean(axis=0)
    Q = P - c
    w, vecs = np.linalg.eigh(Q.T @ Q)
    if w[1] <= 1e-12 * max(w[2], 1e-300):
        return None
    nrm, e1 = vecs[:, 0], vecs[:, 2]
    e2 = np.cross(nrm, e1)
    x, y = Q @ e1, Q @ e2
    A = np.column_stack((2 * x, 2 * y, np.ones(len(x))))
    sol, *_ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
    cx, cy = sol[0], sol[1]
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 0.0))
    for _ in range(15):
        d = np.hypot(x - cx, y - cy)
        if d.min() < 1e-15:
            break
        J = np.column_stack(((cx - x) / d, (cy - y) / d, -np.ones(len(x))))
        step, *_ = np.linalg.lstsq(J, -(d - r), rcond=None)
        cx, cy, r = cx + step[0], cy + step[1], r + step[2]
        if np.abs(step).max() < 1e-13 * max(r, 1e-12):
            break
    d = np.hypot(x - cx, y - cy)
    r = float(d.mean())
    span = float(np.linalg.norm(P.max(axis=0) - P.min(axis=0)))
    if r <= 1e-12 or r > 20.0 * span:
        return None
    max_dev = max(float(np.abs(d - r).max()), float(np.abs(Q @ nrm).max()))
    return c + e1 * cx + e2 * cy, nrm, r, max_dev


class Curve:
    """Fit of one column (an open curve) and everything needed to sample it.

    Models: SUPER (bevel profile in the corner of the surrounding geometry),
    ARC (circular arc), SPLINE (Catmull-Rom through the points; a straight
    line for a single segment). Tension works on every model: in the corner
    frame when there is one, otherwise the curve is cut into pieces that each
    turn at most ~100 degrees and every piece is tightened in its own corner.
    """

    def __init__(self, verts, dA, dB):
        P = np.array([v.co.to_tuple() for v in verts], dtype=np.float64)
        self.P = P
        self.m = m = len(P) - 1
        self.chord = float(np.linalg.norm(P[-1] - P[0]))
        if m >= 2:
            if dA is None:
                dA = _end_tangent(P)
            if dB is None:
                dB = _end_tangent(P[::-1])
        self.frame = _corner(P[0], P[-1], dA, dB)
        self.e0 = None          # profile exponent in the corner frame (None: no usable corner)
        self.super_ok = False
        self.super_dev = math.inf
        self.s_super = None
        self.legs = False       # tension / radius use the corner frame
        self.model = 'SPLINE'
        self.params = np.linspace(0.0, 1.0, m + 1)
        self._tables = {}
        self._uv = None
        self._pieces = {}
        self._fit_arc()

    # -- circle / arc ------------------------------------------------------------

    def _fit_arc(self):
        self.circle = None      # (center, normal): radius scaling without a corner
        self.circle_r = 0.0
        self.arc = None
        self.s_arc = None
        self.arc_ok = False
        self.arc_dev = math.inf
        if self.m < 2:
            return
        fit = _fit_circle(self.P)
        if fit is None:
            return
        O, n, r, dev = fit
        self.circle = (O, n)
        self.circle_r = r
        R = self.P - O
        u = R[0] - n * R[0].dot(n)
        if np.linalg.norm(u) < 1e-12:
            return
        u = u / np.linalg.norm(u)
        v = np.cross(n, u)
        if R[1] @ v < 0.0:
            n, v = -n, -v
            self.circle = (O, n)
        th = np.unwrap(np.arctan2(R @ v, R @ u))
        th = th - th[0]
        total = float(th[-1])
        if not (np.all(np.diff(th) > 1e-9) and 1e-6 < total < 2.0 * math.pi):
            return
        self.arc = (O, u, v, r, total)
        self.s_arc = th / total
        self.arc_dev = dev / r
        self.arc_ok = self.arc_dev <= CURVE_TOLERANCE

    # -- bevel profile (superellipse in the corner frame) ------------------------------

    def prepare_super(self):
        """Profile coordinates of the inner points, or None when the column
        has no usable corner frame. The exponent is then solved for all
        columns at once (see fit_curves) and passed to finish_super()."""
        if self.frame is None:
            return None
        x, y, _ = _frame_coords(self.frame, self.P)
        u, v = 1.0 - x, 1.0 - y
        ui, vi = u[1:-1], v[1:-1]
        if not (np.all(ui > 1e-6) and np.all(ui < 1 - 1e-6)
                and np.all(vi > 1e-6) and np.all(vi < 1 - 1e-6)):
            return None
        self._uv = (u, v)
        return ui, vi

    def finish_super(self, e0):
        u, v = self._uv
        self.e0 = e0
        theta = np.arctan2(np.clip(v, 0.0, None), np.clip(u, 0.0, None))
        cum, th = self._table(e0)
        s = np.interp(theta, th[::-1], cum[::-1])
        s[0], s[-1] = 0.0, 1.0
        if not np.all(np.diff(s) > 1e-9):
            return
        Q = self._super_points(s, e0)
        dev = float(np.linalg.norm(Q - self.P, axis=1).max())
        self.s_super = s
        self.super_dev = dev / max(self.chord, 1e-12)
        self.super_ok = self.super_dev <= CURVE_TOLERANCE

    def _super_at(self, theta, e):
        u, v = _polar_uv(theta, e)
        return _frame_points(self.frame, 1.0 - u, 1.0 - v)

    def _table(self, e):
        """Arc-length fraction (0 at A, 1 at B) against polar angle for the
        profile with exponent e, measured on the real (3D) curve."""
        t = self._tables.get(e)
        if t is None:
            th = np.linspace(HALF_PI, 0.0, 257)
            Q = self._super_at(th, e)
            cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))])
            cum = cum / cum[-1] if cum[-1] > 1e-15 else np.linspace(0.0, 1.0, len(th))
            t = (cum, th)
            self._tables[e] = t
        return t

    def _super_points(self, s, e):
        cum, th = self._table(e)
        return self._super_at(np.interp(s, cum, th), e)

    # -- model ---------------------------------------------------------------------

    def exact_model(self):
        """The closer of the two exact fits (bevel profile or circular arc)."""
        if self.s_super is not None and (self.arc is None or self.super_dev <= self.arc_dev + 1e-3):
            return 'SUPER', self.super_ok
        if self.arc is not None:
            return 'ARC', self.arc_ok
        return 'SPLINE', False

    def set_model(self, model):
        self.model = model
        # The outside corner drives tension and radius unless a circle fits the
        # curve better than the profile does (e.g. part of a ring, whose
        # neighbouring edges are not tangent to it).
        self.legs = self.e0 is not None and model != 'ARC'
        if model == 'SUPER':
            self.params = self.s_super
        elif model == 'ARC':
            self.params = self.s_arc
        else:
            self.params = np.linspace(0.0, 1.0, self.m + 1)

    def _base(self, s):
        s = np.asarray(s, dtype=np.float64)
        if self.model == 'SUPER':
            return self._super_points(s, self.e0)
        if self.model == 'ARC':
            O, u, v, r, total = self.arc
            th = s * total
            return O + r * (np.outer(np.cos(th), u) + np.outer(np.sin(th), v))
        if self.m == 1:
            return self.P[0] + s[:, None] * (self.P[1] - self.P[0])
        return _catmull(self.P, s)

    def _tension_pieces(self):
        """(s_start, s_end, frame, e0) pieces for curves without a corner frame."""
        if self.legs:
            return ()
        pieces = self._pieces.get(self.model)
        if pieces is not None:
            return pieces
        pieces = []
        ss = np.linspace(0.0, 1.0, 257)
        Q = self._base(ss)
        D = np.diff(Q, axis=0)
        ln = np.linalg.norm(D, axis=1)
        if np.all(ln > 1e-15):
            T = D / ln[:, None]
            ang = np.arccos(np.clip(np.einsum('ij,ij->i', T[:-1], T[1:]), -1.0, 1.0))
            turn = float(ang.sum())
            if turn > math.radians(5.0):
                count = int(math.ceil(turn / math.radians(100.0)))
                s_c = np.concatenate([[0.0], ss[1:-1], [1.0]])
                t_c = np.concatenate([[0.0], np.cumsum(ang) - 0.5 * ang, [turn]])
                cuts = [0.0] + [float(np.interp(k * turn / count, t_c, s_c)) for k in range(1, count)] + [1.0]
                for sa, sb in zip(cuts, cuts[1:]):
                    A, B = self._base([sa, sb])
                    tA, tB = self._tangent(sa), self._tangent(sb)
                    if tA is None or tB is None:
                        continue
                    frame = _corner(A, B, tA, -tB)
                    if frame is None:
                        continue
                    x, y, _ = _frame_coords(frame, self._base(np.linspace(sa, sb, 11)[1:-1]))
                    u, v = 1.0 - x, 1.0 - y
                    if not (np.all(u > 1e-6) and np.all(u < 1 - 1e-6) and np.all(v > 1e-6) and np.all(v < 1 - 1e-6)):
                        continue
                    e0 = float(np.exp(np.median(np.log(_solve_exp(u, v)))))
                    pieces.append((sa, sb, frame, e0))
        self._pieces[self.model] = pieces
        return pieces

    def _tangent(self, s):
        """Unit tangent of the base curve at s (direction of increasing s)."""
        if self.model == 'ARC':
            O, u, v, r, total = self.arc
            th = s * total
            return _unit(-math.sin(th) * u + math.cos(th) * v)
        h = 1e-5
        a, b = max(s - h, 0.0), min(s + h, 1.0)
        Q = self._base([a, b])
        return _unit(Q[1] - Q[0])

    @property
    def profile(self):
        if self.legs:
            return exp_to_profile(self.e0)
        ps = [exp_to_profile(p[3]) for p in self._tension_pieces()]
        return float(np.median(ps)) if ps else None

    def eval(self, s, shift=0.0):
        s = np.asarray(s, dtype=np.float64)
        if not shift:
            return self._base(s)
        if self.model == 'SUPER':
            return self._super_points(s, _shift_exp(self.e0, shift))
        Q = self._base(s)
        if self.legs:
            return _tighten(self.frame, Q, self.e0, _shift_exp(self.e0, shift))
        if shift < 0.0:
            return self._flatten(Q, shift)
        # Tighter: each piece toward its own corner (a half circle toward a box);
        # the pieces stay tangent to each other where they meet.
        for sa, sb, frame, e0 in self._tension_pieces():
            mask = (s >= sa) & (s <= sb)
            if mask.any():
                Q[mask] = _tighten(frame, Q[mask], e0, _shift_exp(e0, shift))
        return Q

    def _flatten(self, Q, shift):
        """Flatter, for curves without a corner: pull the whole curve toward
        the straight line between its ends. Profile 0.25 is that line; lower
        values bulge the other way."""
        p0 = self.profile
        if p0 is None or p0 <= 0.26:
            return Q
        p = min(max(p0 + shift, PROFILE_MIN), PROFILE_MAX)
        f = max((p - 0.25) / (p0 - 0.25), -1.0)
        A, B = self.P[0], self.P[-1]
        d = B - A
        dd = d.dot(d)
        if dd < 1e-24:
            return Q
        L = A + np.outer(((Q - A) @ d) / dd, d)
        return L + f * (Q - L)

    # -- radius --------------------------------------------------------------------

    def radius_center(self):
        """(center, plane normal or None). A bevel scales about its corner, so
        its ends slide along the faces it joins; an arc about its circle center."""
        if self.legs:
            return self.frame[0], None
        if self.circle is not None:
            return self.circle
        return None

    def radius(self):
        """Size the radius drag works on: the leg length for a bevel (its
        width for a square corner), the circle radius for an arc."""
        if self.legs:
            _, a, b = self.frame
            return 0.5 * float(np.linalg.norm(a) + np.linalg.norm(b))
        if self.circle is not None:
            return self.circle_r
        return 0.0

    def _scaled(self, pts, k):
        rc = self.radius_center()
        if rc is None or k == 1.0:
            return pts
        O, n = rc
        d = pts - O
        if n is None:
            return O + k * d
        h = np.outer(d @ n, n)
        return O + h + k * (d - h)

    # -- sampling ------------------------------------------------------------------

    def new_points(self, M, even=False, shift=0.0, scale=1.0):
        """M + 1 points along the curve. The end points stay where they are,
        unless the radius is scaled."""
        m = self.m
        if even:
            S = 16 * max(m, M) + 1
            ss = np.linspace(0.0, 1.0, S)
            Q = self.eval(ss, shift)
            cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))])
            L = cum[-1]
            if L > 1e-15:
                # Equal edge (chord) lengths: start from equal arc lengths and
                # correct each span by its chord/arc ratio a few times.
                targets = np.linspace(0.0, L, M + 1)
                for _ in range(4):
                    s_new = np.interp(targets, cum, ss)
                    d = np.linalg.norm(np.diff(self.eval(s_new, shift), axis=0), axis=1)
                    a = np.diff(targets)
                    inv = a / np.maximum(d, 1e-15 * L)
                    targets = np.concatenate([[0.0], np.cumsum(inv * (L / inv.sum()))])
                    targets[-1] = L
                s_new = np.interp(targets, cum, ss)
            else:
                s_new = np.linspace(0.0, 1.0, M + 1)
        else:
            s_new = np.interp(np.arange(M + 1) * (m / M), np.arange(m + 1), self.params)
        pts = self.eval(s_new, shift)
        pts[0] = self.P[0]
        pts[-1] = self.P[-1]
        return self._scaled(pts, scale)


# -----------------------------------------------------------------------------
# Analysis


def fit_curves(cvs):
    """Fit the profile exponent of every curve (one batched solve)."""
    pend = [(cv, cv.prepare_super()) for cv in cvs]
    pend = [(cv, r) for cv, r in pend if r is not None]
    inner = [(cv, r) for cv, r in pend if len(r[0])]
    if inner:
        es = np.log(_solve_exp(np.concatenate([r[0] for _, r in inner]),
                               np.concatenate([r[1] for _, r in inner])))
        pos = 0
        for cv, r in inner:
            k = len(r[0])
            cv.finish_super(float(np.exp(np.median(es[pos:pos + k]))))
            pos += k
    for cv, r in pend:
        if not len(r[0]):
            cv.finish_super(1.0)  # a single segment is a straight chamfer


def _column_normal(col):
    """Normal of the plane through a column's points, or None (straight or
    single-segment columns)."""
    if len(col) < 3:
        return None
    P = np.array([v.co.to_tuple() for v in col], dtype=np.float64)
    Q = P - P.mean(axis=0)
    w, vecs = np.linalg.eigh(Q.T @ Q)
    if w[1] <= 1e-10 * max(w[2], 1e-300):
        return None
    return vecs[:, 0]


def _fit_band(band, fit_mode, cache=None):
    m = band.m
    bf = band.faces()
    K = len(band.columns)
    cols = band.columns

    def tangent(c, r):
        prev = c - 1 if (c > 0 or band.closed) else None
        nxt = c + 1 if (c < K - 1 or band.closed) else None
        if K < 2 or (prev is None and nxt is None):
            return None
        a = cols[prev % K][r] if prev is not None else cols[c][r]
        b = cols[nxt % K][r] if nxt is not None else cols[c][r]
        return _unit(_np(b.co) - _np(a.co))

    band.curves = []
    fresh = []
    for c, col in enumerate(cols):
        key = tuple(v.index for v in col)
        cv = cache.get(key) if cache is not None else None
        if cv is None:
            # Legs are taken in the column's own plane (the profile plane),
            # which rows curving around a corner don't tilt; a single-segment
            # column has no plane of its own, so the row direction stands in.
            n = _column_normal(col)
            dA, kA = _leg_dir(col[0], col[1], bf, n if n is not None else tangent(c, 0))
            dB, kB = _leg_dir(col[m], col[m - 1], bf, n if n is not None else tangent(c, m))
            if kA == 'quad' and kB == 'quad':
                # Both ends only touch plain quads: a plain corner edge of a
                # box, not a bevel or chamfer. No corner to work with.
                dA = dB = None
            cv = Curve(col, dA, dB)
            fresh.append(cv)
            if cache is not None:
                cache[key] = cv
        band.curves.append(cv)
    fit_curves(fresh)

    cvs = band.curves
    if fit_mode == FIT_SPLINE:
        models = ['SPLINE'] * K
    else:
        exact = [cv.exact_model() for cv in cvs]
        if fit_mode == FIT_CIRCLE or all(ok for _, ok in exact):
            models = [md for md, _ in exact]
        else:
            models = ['SPLINE'] * K
    band.mode = FIT_SPLINE if all(md == 'SPLINE' for md in models) else FIT_CIRCLE
    for cv, md in zip(cvs, models):
        cv.set_model(md)
    sample = cvs[::max(1, K // 8)]
    profiles = [p for p in (cv.profile for cv in sample) if p is not None]
    band.profile = float(np.median(profiles)) if profiles else None


def analyze(bm, fit_mode=FIT_AUTO, cache=None):
    """Bands for every open path in the selection (one band per quad band;
    several selected columns of the same band count once).

    ``cache``: a dict kept between calls on identical geometry (the modal
    tool restores the same snapshot on every step); the curve fits are
    reused instead of being computed again."""
    bands = []
    claimed_v = set()
    claimed_f = set()
    claimed_cols = set()
    for path in extract_selected_paths(bm):
        if tuple(path) in claimed_cols or tuple(reversed(path)) in claimed_cols:
            continue
        band = build_band(path)
        if band is None:
            continue
        inner = band.interior()
        faces = band.faces()
        edges = set(e for ce in band.col_edges for e in ce)
        if inner & claimed_v or faces & claimed_f or edges & claimed_v:
            continue
        claimed_v |= inner
        claimed_v |= edges  # edges and verts never compare equal; one set is fine
        claimed_f |= faces
        for col in band.columns:
            claimed_cols.add(tuple(col))
        bands.append(band)
    if cache is not None:
        bm.verts.index_update()
    for band in bands:
        _fit_band(band, fit_mode, cache)
    return bands


# -----------------------------------------------------------------------------
# Rebuild


def _kmap(i, m, M):
    """Old row i -> nearest new row."""
    if i <= 0:
        return 0
    if i >= m:
        return M
    k = _round_half_up(i * M / m)
    return min(max(k, 1), M - 1) if M >= 2 else min(max(k, 0), M)


def _restitch(bm, F, lookup, bands, jobs):
    """Rebuild a face touching resampled columns: each run of consecutive
    column vertices is replaced by the matching run of new vertices."""
    loops = list(F.loops)
    L = len(loops)
    verts = [l.vert for l in loops]
    info = [lookup.get(v, ()) for v in verts]

    def link(p):
        q = (p + 1) % L
        for b, c, i in info[p]:
            for b2, c2, i2 in info[q]:
                if b == b2 and c == c2 and abs(i2 - i) == 1:
                    return (b, c, i2 - i)
        return None

    links = [link(p) for p in range(L)]
    start = None
    for p in range(L):
        if links[(p - 1) % L] is None:
            start = p
            break
    if start is None:
        return None

    order = [(start + t) % L for t in range(L)]
    out, prov = [], []
    t = 0
    while t < L:
        p = order[t]
        run = [p]
        key = None
        while t + 1 < L and links[order[t]] is not None and (key is None or links[order[t]] == key):
            key = links[order[t]]
            t += 1
            run.append(order[t])
        t += 1
        if key is None:
            inner = [(b, c, i) for b, c, i in info[p] if 0 < i < bands[b].m]
            if inner:
                b, c, i = inner[0]
                band = bands[b]
                out.append(band.W[c][_kmap(i, band.m, band.M)])
            else:
                out.append(verts[p])
            prov.append(p)
            continue
        b, c, d = key
        band = bands[b]
        i_s = next(i for bb, cc, i in info[run[0]] if bb == b and cc == c)
        i_e = i_s + d * (len(run) - 1)
        ks, ke = _kmap(i_s, band.m, band.M), _kmap(i_e, band.m, band.M)
        step = 1 if ke >= ks else -1
        ks_list = list(range(ks, ke + step, step))
        for n, k in enumerate(ks_list):
            out.append(band.W[c][k])
            if n == 0:
                prov.append(run[0])
            elif n == len(ks_list) - 1:
                prov.append(run[-1])
            else:
                prov.append(None)
        if len(ks_list) == 1:
            prov[-1] = None

    vs, pv = [], []
    for v, p in zip(out, prov):
        if vs and vs[-1] == v:
            pv[-1] = None
            continue
        vs.append(v)
        pv.append(p)
    while len(vs) > 1 and vs[0] == vs[-1]:
        vs.pop()
        pv.pop()
        pv[0] = None
    if len(vs) < 3 or len(set(vs)) != len(vs):
        return None

    f = jobs.face(bm, vs, F)
    if f is None:
        return None
    n = len(vs)
    for i in range(n):
        pa, pb = pv[i], pv[(i + 1) % n]
        if pa is None or pb is None or (pa + 1) % L != pb:
            continue
        jobs.edge(bm.edges.get((vs[i], vs[(i + 1) % n])), loops[pa].edge)
    return f


def _rebuild_band(bm, bid, band, M, pts, jobs, lookup):
    m = band.m
    K = len(band.columns)
    W = []
    for c, col in enumerate(band.columns):
        P = pts[c]
        row = [col[0]]
        for k in range(1, M):
            co = Vector(P[k].tolist())
            w = bm.verts.new(co)
            x = k * m / M
            i = min(int(math.floor(x)), m - 1)
            sel = col[_round_half_up(x)].select
            jobs.verts.append((w, col[i], col[i + 1], x - i, co, sel))
            row.append(w)
        row.append(col[m])
        W.append(row)
        for i, v in enumerate(col):
            lookup.setdefault(v, []).append((bid, c, i))
        for k in range(M):
            e = _get_edge(bm, row[k], row[k + 1])
            i = min(m - 1, int((k + 0.5) * m / M))
            jobs.edge(e, band.col_edges[c][i])
    band.W = W
    band.M = M

    for c, strip in enumerate(band.strips):
        c1 = (c + 1) % K
        A, B = W[c], W[c1]
        colA, colB = band.columns[c], band.columns[c1]
        for k in range(M):
            i = min(m - 1, int((k + 0.5) * m / M))
            fo = strip[i]
            if _face_goes(fo, colA[i], colA[i + 1]):
                vs = [A[k], A[k + 1], B[k + 1], B[k]]
            else:
                vs = [A[k + 1], A[k], B[k], B[k + 1]]
            jobs.face(bm, vs, fo)
        # Edges along the new rows: data from the nearest old interior row;
        # seam / sharp marks only go to the one new row closest to each old row.
        if m >= 2 and M >= 2:
            rows = {}
            for k in range(1, M):
                x = k * m / M
                i = min(max(_round_half_up(x), 1), m - 1)
                rows.setdefault(i, []).append((abs(x - i), k))
            for i, ks in rows.items():
                src = edge_between(colA[i], colB[i])
                if src is None:
                    continue
                prim = min(ks)[1]
                for _, k in ks:
                    e = bm.edges.get((A[k], B[k]))
                    if k == prim:
                        jobs.edge(e, src)
                    else:
                        jobs.edge(e, src, seam=False, smooth=True)


def _column_scales(band, scale):
    if scale == 1.0 or not band.has_radius():
        return [1.0] * len(band.curves)
    radii = [cv.radius() for cv in band.curves]
    ref = radii[band.sel] if radii[band.sel] > 0.0 else max(radii, default=0.0)
    delta = (scale - 1.0) * ref
    return [max((r + delta) / r, 1e-3) if r > 0.0 else 1.0 for r in radii]


def collapse(bm, bands):
    """Merge every column of the bands into one vertex: at the corner for a
    bevel profile (the bevel turns back into a sharp edge), at the circle
    center for an arc, at the chord middle otherwise."""
    import bmesh

    merged = []
    for band in bands:
        if not band.has_radius():
            continue  # e.g. a plain straight edge: nothing to collapse
        coords, radii = [], []
        for cv, col in zip(band.curves, band.columns):
            co = cv.radius_center()[0]
            coords.append(tuple(co.tolist()))
            radii.append(cv.radius())
            verts = [v for v in col if v.is_valid]
            if len(verts) < 2:
                continue
            bmesh.ops.pointmerge(bm, verts=verts, merge_co=Vector(co.tolist()))
            merged.extend(v for v in verts if v.is_valid)
        # What it takes to bring the curve back (see restore.py).
        band.collapsed = (coords, radii)
    if not merged:
        return 0
    for v in merged:
        v.select = True
    if 'VERT' not in bm.select_mode:
        ms = set(merged)
        for e in bm.edges:
            if e.verts[0] in ms and e.verts[1] in ms:
                e.select = True
    bm.select_flush_mode()
    bm.normal_update()
    return len(bands)


def resample(bm, bands, counts, even=False, shift=0.0, force=False, scale=1.0):
    """Resample ``bands`` (from ``analyze`` on this bm) to ``counts`` segments.
    ``even``: evenly spaced points; ``shift``: profile change (tension);
    ``scale``: radius factor of the selected curve. The other columns of its
    band get the same absolute change, so parallel columns (a bevel and the
    loops wrapping around its end) stay parallel. The curves' ends move too.
    A scale of 0 is the stop point: every column is merged into a single
    vertex (see collapse()), which removes a bevel.
    Returns the number of bands changed."""
    import bmesh

    if scale <= 0.0:
        return collapse(bm, bands)

    rebuild = []
    moved = 0
    for band, M in zip(bands, counts):
        if M is None:
            continue
        M = max(MIN_SEGMENTS, min(MAX_SEGMENTS, int(M)))
        if M == band.m and not (even or shift or force or scale != 1.0):
            continue
        pts = [cv.new_points(M, even, shift, k) for cv, k in zip(band.curves, _column_scales(band, scale))]
        if scale != 1.0:
            for col, P in zip(band.columns, pts):
                col[0].co = Vector(P[0].tolist())
                col[-1].co = Vector(P[-1].tolist())
        if M == band.m:
            # Same count: only move the vertices.
            for col, P in zip(band.columns, pts):
                for i in range(1, M):
                    col[i].co = Vector(P[i].tolist())
            moved += 1
            continue
        rebuild.append((band, M, pts))

    if not rebuild:
        if moved:
            bm.normal_update()
        return moved

    jobs = _Jobs()
    lookup = {}
    handled = set()
    old_inner = []
    old_col_edges = []
    live = []
    for band, M, pts in rebuild:
        bid = len(live)
        live.append(band)
        _rebuild_band(bm, bid, band, M, pts, jobs, lookup)
        handled |= band.faces()
        for col in band.columns:
            old_inner.extend(col[1:-1])
        for ce in band.col_edges:
            old_col_edges.extend(ce)

    affected = []
    seen = set(handled)
    for e in old_col_edges:
        for f in e.link_faces:
            if f not in seen:
                seen.add(f)
                affected.append(f)
    for v in old_inner:
        for f in v.link_faces:
            if f not in seen:
                seen.add(f)
                affected.append(f)
    for F in affected:
        _restitch(bm, F, lookup, live, jobs)

    # Loose edges hanging off inner column vertices.
    def mapped(v):
        for b, c, i in lookup.get(v, ()):
            band = live[b]
            if 0 < i < band.m:
                return band.W[c][_kmap(i, band.m, band.M)]
        return v

    col_edge_set = set(old_col_edges)
    for v in old_inner:
        for e in v.link_edges:
            if e.link_faces or e in col_edge_set:
                continue
            a, b = mapped(v), mapped(e.other_vert(v))
            if a != b:
                jobs.edge(_get_edge(bm, a, b), e)

    old_faces = list(handled) + affected
    sel_verts, sel_edges, sel_faces = jobs.run(bm)
    bmesh.ops.delete(bm, geom=list(set(old_inner)), context='VERTS')
    faces_left = [f for f in old_faces if f.is_valid]
    if faces_left:
        bmesh.ops.delete(bm, geom=faces_left, context='FACES_ONLY')
    edges_left = [e for e in old_col_edges if e.is_valid]
    if edges_left:
        bmesh.ops.delete(bm, geom=edges_left, context='EDGES')

    mode = bm.select_mode
    if 'VERT' in mode:
        for v in sel_verts:
            v.select = True
    elif 'EDGE' in mode:
        for e in sel_edges:
            e.select = True
    else:
        for f in sel_faces:
            f.select = True
    bm.select_flush_mode()
    bm.normal_update()
    return len(rebuild) + moved
