# SPDX-FileCopyrightText: 2026 Robin Mariančík
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Core geometry logic for Roundabout.

Pure BMesh code: no operators, no UI. It works on any BMesh (edit-mode or
standalone), which keeps it testable in background Blender.

Overview
--------
1. ``extract_selected_rings`` turns the edge selection into closed, ordered
   vertex loops ("rings").
2. ``build_chain`` grows each ring into a *chain*: every ring that is
   connected to it through clean quad strips (side walls, bevel segments,
   inset annuli, the inner wall of a pipe ...). All rings of a chain share the
   same vertex count and an index alignment (vertex k of every ring is joined
   to vertex k of the next ring by a rail edge), so they must be resampled
   together.
3. Every ring is fitted (circle fit when the ring is round, a periodic spline
   through the original points otherwise) so new vertices land on the true
   curve instead of on the old flat facets.
4. ``resample`` builds the new rings, the quad strips between them, rebuilds
   n-gon and triangle-fan caps, and re-stitches any other face touching the
   rings (e.g. the geometry a cylinder is merged into). Face, loop (UV,
   color...), edge and vertex data are copied/interpolated from the original
   elements before those are deleted.
"""

import math
from bisect import bisect_right

import numpy as np
from mathutils import Vector

TAU = 2.0 * math.pi

FIT_AUTO = 'AUTO'
FIT_CIRCLE = 'CIRCLE'
FIT_SPLINE = 'SPLINE'

# A ring counts as "round" when every vertex is within this fraction of the
# radius from the fitted circle (both in-plane and out-of-plane).
CIRCLE_TOLERANCE = 0.01

MIN_SEGMENTS = 3
MAX_SEGMENTS = 2048


def _round_half_up(x):
    return int(math.floor(x + 0.5))


def edge_between(a, b):
    for e in a.link_edges:
        if e.other_vert(a) == b:
            return e
    return None


# -----------------------------------------------------------------------------
# Rings


class Ring:
    """An ordered closed loop of vertices plus its curve fit."""

    def __init__(self, verts):
        self.verts = list(verts)
        self.n = len(self.verts)
        n = self.n
        self.edges = [edge_between(self.verts[k], self.verts[(k + 1) % n]) for k in range(n)]
        self.vset = set(self.verts)
        # Filled by fit()
        self.pts = None
        self.center = None
        self.normal = None
        self.u = None
        self.v = None
        self.radius = 0.0
        self.circle_ok = False
        self.circle_params = None
        self.thetas = None    # angle of each old vertex (from vertex 0), when monotonic
        self.circle_phase = 0.0
        self.index_params = [j / n for j in range(n)]
        # Filled by set_mode()
        self.mode = FIT_SPLINE
        self.params = self.index_params
        self.phase = 0.0
        # Radial scale about the fitted center, in the ring's plane (set by resample()).
        self.scale = 1.0
        # Filled by resample()
        self.N = n
        self.W = None

    @property
    def valid(self):
        return self.n >= 3 and all(e is not None for e in self.edges)

    # -- fitting -------------------------------------------------------------

    def fit(self):
        """Best-fit circle of the ring (least squares in every step, so fixing
        a ring moves its vertices as little as possible):

        * plane: least-squares plane through the vertices (PCA), oriented so
          the ring order runs counter-clockwise around the normal;
        * circle: geometric fit (minimises the sum of squared distances to the
          circle) refined with Gauss-Newton from an algebraic start;
        * phase: rotation of an evenly spaced polygon that best matches the
          current vertex angles.
        """
        pts = [v.co.copy() for v in self.verts]
        self.pts = pts
        n = self.n
        P = np.array([p.to_tuple() for p in pts], dtype=np.float64)
        c = P.mean(axis=0)
        Q = P - c

        # Newell normal decides the orientation (ring order is CCW around it).
        nv = np.cross(Q, np.roll(Q, -1, axis=0)).sum(axis=0)
        if np.linalg.norm(nv) < 1e-12:
            self.circle_ok = False
            return
        # Least-squares plane normal: smallest principal axis.
        w, vecs = np.linalg.eigh(Q.T @ Q)
        nrm = vecs[:, 0]
        if w[1] < 1e-18:  # collinear points
            self.circle_ok = False
            return
        if nrm.dot(nv) < 0.0:
            nrm = -nrm

        u0 = Q[0] - nrm * Q[0].dot(nrm)
        if np.linalg.norm(u0) < 1e-12:
            u0 = np.array(Vector(nrm).orthogonal())
        u0 = u0 / np.linalg.norm(u0)
        v0 = np.cross(nrm, u0)
        x = Q @ u0
        y = Q @ v0

        # Algebraic (Kasa) start, centroid-relative so the linear terms vanish.
        z = x * x + y * y
        sxx, sxy, syy = x.dot(x), x.dot(y), y.dot(y)
        det = sxx * syy - sxy * sxy
        cx = cy = 0.0
        if abs(det) > 1e-18:
            sxz, syz = x.dot(z), y.dot(z)
            cx = 0.5 * (sxz * syy - syz * sxy) / det
            cy = 0.5 * (sxx * syz - sxy * sxz) / det
        d = np.hypot(x - cx, y - cy)
        r = d.mean()

        # Geometric refinement: minimise sum (|p - c| - r)^2.
        scale = max(r, 1e-12)
        for _ in range(25):
            d = np.hypot(x - cx, y - cy)
            if d.min() < 1e-15:
                break
            J = np.column_stack(((cx - x) / d, (cy - y) / d, -np.ones(n)))
            f = d - r
            step, *_ = np.linalg.lstsq(J, -f, rcond=None)
            cx += step[0]
            cy += step[1]
            r += step[2]
            if np.abs(step).max() < 1e-13 * scale:
                break
        d = np.hypot(x - cx, y - cy)
        r = float(d.mean())

        center_np = c + u0 * cx + v0 * cy
        heights = Q @ nrm - (center_np - c).dot(nrm)
        max_plane = float(np.abs(heights).max())

        u = (P[0] - center_np)
        u = u - nrm * u.dot(nrm)
        if np.linalg.norm(u) < 1e-12 or r < 1e-12:
            self.circle_ok = False
            return
        u = u / np.linalg.norm(u)
        v = np.cross(nrm, u)
        R = P - center_np
        thetas = np.mod(np.arctan2(R @ v, R @ u), TAU)
        thetas[0] = 0.0
        mono = bool(np.all(np.diff(thetas) > 0.0))

        # Best rotation of an evenly spaced n-gon against the current angles.
        off = thetas - TAU * np.arange(n) / n
        phase = math.atan2(np.sin(off).sum(), np.cos(off).sum()) / TAU

        self.center = Vector(center_np.tolist())
        self.normal = Vector(nrm.tolist())
        self.u = Vector(u.tolist())
        self.v = Vector(v.tolist())
        self.radius = r
        max_dev = float(np.abs(d - r).max())
        self.circle_ok = (
            mono
            and max_dev <= CIRCLE_TOLERANCE * r
            and max_plane <= CIRCLE_TOLERANCE * r
        )
        self.circle_params = (thetas / TAU).tolist() if mono else None
        self.thetas = thetas.tolist() if mono else None
        self.circle_phase = phase

    def set_mode(self, mode):
        """mode: FIT_CIRCLE or FIT_SPLINE (already resolved from AUTO)."""
        if mode == FIT_CIRCLE and self.center is not None:
            self.mode = FIT_CIRCLE
            self.params = self.circle_params or self.index_params
            self.phase = self.circle_phase
        else:
            self.mode = FIT_SPLINE
            self.params = self.index_params
            self.phase = 0.0

    # -- parameter helpers ------------------------------------------------------
    # Curve parameter t in [0, 1): old vertex j sits at params[j] (params[0] is
    # 0). New vertex k of N sits at k / N + phase.

    def new_param(self, k, N, offset=0.0):
        return ((k + offset) / N + self.phase) % 1.0

    def locate(self, t):
        """Old segment j containing curve parameter t and the fraction along it."""
        t %= 1.0
        params = self.params
        j = bisect_right(params, t) - 1
        if j < 0:
            j = 0
        p0 = params[j]
        p1 = params[j + 1] if j + 1 < self.n else 1.0
        span = p1 - p0
        fr = (t - p0) / span if span > 1e-15 else 0.0
        return j, min(max(fr, 0.0), 1.0)

    def nearest_new(self, j, N):
        """Index of the new vertex closest to old vertex j."""
        return _round_half_up((self.params[j] - self.phase) * N) % N

    def nearest_old(self, k, N):
        j, fr = self.locate(self.new_param(k, N))
        return j if fr < 0.5 else (j + 1) % self.n

    # -- sampling --------------------------------------------------------------

    def sample(self, N):
        out = self._sample(N)
        if self.scale != 1.0:
            out = [self._scaled(p) for p in out]
        return out

    def _scaled(self, p):
        c = self.center
        if c is None:
            c = sum(self.pts, Vector()) / self.n
        d = p - c
        if self.normal is not None:
            h = self.normal * d.dot(self.normal)
            return c + h + (d - h) * self.scale
        return c + d * self.scale

    def _sample(self, N):
        if self.mode == FIT_CIRCLE:
            c, u, v, r = self.center, self.u, self.v, self.radius
            out = []
            for k in range(N):
                a = TAU * (k / N + self.phase)
                out.append(c + (u * math.cos(a) + v * math.sin(a)) * r)
            return out
        # Uniform periodic Catmull-Rom through the original points.
        pts = self.pts
        n = self.n
        out = []
        for k in range(N):
            x = ((k / N + self.phase) % 1.0) * n
            j = int(math.floor(x))
            s = x - j
            p0 = pts[(j - 1) % n]
            p1 = pts[j % n]
            p2 = pts[(j + 1) % n]
            p3 = pts[(j + 2) % n]
            s2 = s * s
            s3 = s2 * s
            out.append(
                0.5 * ((2.0 * p1)
                       + (p2 - p0) * s
                       + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * s2
                       + (3.0 * p1 - p0 - 3.0 * p2 + p3) * s3)
            )
        return out


# -----------------------------------------------------------------------------
# Selection -> rings


def _shares_small_face(e1, e2):
    """True when both edges belong to the same triangle or quad."""
    f1 = set(e1.link_faces)
    for f in e2.link_faces:
        if f in f1 and len(f.verts) <= 4:
            return True
    return False


def _walk_ring(e0, sel):
    start = e0.verts[0]
    prev = start
    v = e0.verts[1]
    verts = [start]
    visited = {start}
    e_in = e0
    limit = len(sel) + 1
    steps = 0
    while v != start:
        steps += 1
        if v in visited or steps > limit:
            return None
        visited.add(v)
        verts.append(v)
        cands = [e for e in v.link_edges if e != e_in and e in sel]
        if not cands:
            return None
        if len(cands) > 1:
            # Several selected edges here (adjacent rings selected in vertex or
            # face mode). Continue along the edge loop: skip edges that share a
            # quad/tri with the incoming edge (those are rails).
            cands = [e for e in cands if not _shares_small_face(e_in, e)]
            if not cands:
                return None
            if len(cands) > 1:
                d_in = (v.co - prev.co)
                if d_in.length > 0.0:
                    d_in.normalize()

                def straightness(e):
                    d = e.other_vert(v).co - v.co
                    return d.normalized().dot(d_in) if d.length > 0.0 else -2.0

                cands.sort(key=straightness, reverse=True)
        e_in = cands[0]
        prev = v
        v = e_in.other_vert(v)
    if len(verts) < 3:
        return None
    return verts


def extract_selected_rings(bm):
    """Ordered vertex lists of all closed loops found in the edge selection."""
    sel_list = [e for e in bm.edges if e.select and not e.hide]
    sel = set(sel_list)
    used_verts = set()
    rings = []
    for e0 in sel_list:
        if e0.verts[0] in used_verts or e0.verts[1] in used_verts:
            continue
        verts = _walk_ring(e0, sel)
        if verts is None:
            continue
        if any(v in used_verts for v in verts):
            continue
        used_verts.update(verts)
        rings.append(verts)
    return rings


def select_similar_loops(bm, counts, round_only):
    """Select every closed loop whose vertex count is in ``counts`` (walked
    like a ring: through vertices, skipping edges that share a tri/quad with
    the incoming edge). With ``round_only`` only loops that fit a circle are
    taken. Returns the number of loops newly selected."""
    if not counts:
        return 0
    maxn = max(counts)
    visited = set()
    added = 0
    for e0 in bm.edges:
        if e0 in visited or e0.hide:
            continue
        start = e0.verts[0]
        prev, v, e_in = start, e0.verts[1], e0
        verts, edges, seen = [start], [e0], {start}
        closed = False
        while len(verts) <= maxn:
            if v is start:
                closed = True
                break
            if v in seen or v.hide:
                break
            seen.add(v)
            verts.append(v)
            cands = [e for e in v.link_edges
                     if e is not e_in and not e.hide and not _shares_small_face(e_in, e)]
            if not cands:
                break
            if len(cands) > 1:
                d_in = v.co - prev.co
                if d_in.length > 0.0:
                    d_in.normalize()

                def straightness(e):
                    d = e.other_vert(v).co - v.co
                    return d.normalized().dot(d_in) if d.length > 0.0 else -2.0

                cands.sort(key=straightness, reverse=True)
            e_in = cands[0]
            edges.append(e_in)
            prev, v = v, e_in.other_vert(v)
        visited.update(edges)
        if not closed or len(verts) not in counts or all(e.select for e in edges):
            continue
        ring = Ring(verts)
        if not ring.valid:
            continue
        ring.fit()
        if ring.center is None or ring.thetas is None or (round_only and not ring.circle_ok):
            continue
        for e in edges:
            e.select = True
        for x in verts:
            x.select = True
        added += 1
    return added


# -----------------------------------------------------------------------------
# Rings -> chains


def _face_goes(face, a, b):
    """True if the face winding runs a -> b."""
    for l in face.loops:
        if l.vert == a:
            return l.link_loop_next.vert == b
    return False


def _walk_strip(ring, f0):
    """Follow a quad strip around ``ring`` starting with face f0 (adjacent to
    ring edge 0). Returns (faces, opposite_verts) aligned with the ring
    indices, or None if that side is not a clean quad strip."""
    n = ring.n
    verts = ring.verts
    vset = ring.vset
    faces = []
    opp = [None] * n
    f = f0
    for k in range(n):
        e = ring.edges[k]
        if f is None or len(f.verts) != 4:
            return None
        l = None
        for lp in f.loops:
            if lp.edge == e:
                l = lp
                break
        if l is None:
            return None
        vk = verts[k]
        if l.vert == vk:
            b = l.link_loop_next.link_loop_next.vert  # next to v[k+1]
            a = l.link_loop_prev.vert                 # next to v[k]
            rail = l.link_loop_next.edge
        else:
            a = l.link_loop_next.link_loop_next.vert
            b = l.link_loop_prev.vert
            rail = l.link_loop_prev.edge
        if a in vset or b in vset:
            return None
        if opp[k] is None:
            opp[k] = a
        elif opp[k] != a:
            return None
        k1 = (k + 1) % n
        if opp[k1] is None:
            opp[k1] = b
        elif opp[k1] != b:
            return None
        faces.append(f)
        if k < n - 1:
            others = [x for x in rail.link_faces if x != f]
            if len(others) != 1:
                return None
            f = others[0]
            if ring.edges[k + 1] not in f.edges:
                return None
    if len(set(opp)) != n or len(set(faces)) != n:
        return None
    return faces, opp


def _find_strips(ring, exclude=()):
    out = []
    e0 = ring.edges[0]
    if len(e0.link_faces) > 2:
        return out  # non-manifold ring, don't guess
    for f in e0.link_faces:
        if f in exclude:
            continue
        res = _walk_strip(ring, f)
        if res is not None:
            out.append(res)
    return out


class Chain:
    def __init__(self):
        self.rings = []
        self.strips = []      # strips[i]: faces between rings[i] and rings[i+1]
        # Quad strips from a chain ring to an unselected neighbour ring that is
        # kept as is: (chain_ring, faces, neighbour_verts aligned with the ring).
        self.transitions = []
        self.closed = False
        self.caps = []        # list of ('NGON', ring, face) / ('FAN', ring, apex, tris)
        self.zips = []        # ZipBand: quad/tri strips to untouched neighbour loops
        self.n = 0
        self.mode = FIT_SPLINE

    def all_verts(self):
        s = set()
        for r in self.rings:
            s.update(r.verts)
        return s


def build_chain(start_verts, allowed=None):
    """Grow a chain from a ring through quad strips.

    ``allowed``: when given (a set of vertices), the chain only takes in rings
    made entirely of these vertices; a strip leading to any other ring is
    recorded as a transition and that neighbour ring is left untouched.
    """
    start = Ring(start_verts)
    if not start.valid:
        return None
    chain = Chain()
    chain.n = start.n
    rings = [start]
    strips = []
    claimed = set(start.verts)
    transitions = []

    sides = _find_strips(start)

    def extend(first_strip, forward):
        nonlocal rings, strips
        strip = first_strip
        cur = start
        while strip is not None:
            faces, opp = strip
            first = rings[0] if forward else rings[-1]
            if opp == first.verts and len(rings) > 1:
                return faces  # closed loop back onto the far end
            if any(v in claimed for v in opp):
                return None
            if allowed is not None and not all(v in allowed for v in opp):
                transitions.append((cur, faces, opp))
                return None
            nxt = Ring(opp)
            if not nxt.valid:
                return None
            claimed.update(opp)
            if forward:
                rings.append(nxt)
                strips.append(faces)
            else:
                rings.insert(0, nxt)
                strips.insert(0, faces)
            cur = nxt
            nxt_sides = _find_strips(cur, exclude=set(faces))
            strip = nxt_sides[0] if nxt_sides else None
        return None

    closing = None
    if sides:
        closing = extend(sides[0], True)
    if closing is not None:
        strips.append(closing)
        chain.closed = True
    elif len(sides) > 1:
        # Walk the other direction. Rings get prepended, strips too.
        back_faces = set(sides[1][0])
        if not (back_faces & set(f for s in strips for f in s)):
            extend(sides[1], False)

    chain.rings = rings
    chain.strips = strips
    chain.transitions = transitions
    _detect_caps(chain)
    return chain


def _detect_caps(chain):
    strip_faces = set(f for s in chain.strips for f in s)
    strip_faces.update(f for _, faces, _ in chain.transitions for f in faces)
    caps = []
    for ring in chain.rings:
        vset = ring.vset
        n = ring.n
        seen = set()
        apex_map = {}
        for k, e in enumerate(ring.edges):
            for f in e.link_faces:
                if f in strip_faces:
                    continue
                fl = len(f.verts)
                if fl == n and f not in seen and all(v in vset for v in f.verts):
                    seen.add(f)
                    caps.append(('NGON', ring, f))
                elif fl == 3:
                    apex = [v for v in f.verts if v not in vset]
                    if len(apex) == 1:
                        segs = apex_map.setdefault(apex[0], {})
                        segs[k] = None if k in segs else f
        for apex, segs in apex_map.items():
            if len(segs) == n and all(segs.get(k) is not None for k in range(n)):
                caps.append(('FAN', ring, apex, [segs[k] for k in range(n)]))
    chain.caps = caps


def analyze(bm, fit_mode=FIT_AUTO, whole=False):
    """Find all resample-able chains from the current edge selection.

    whole=False: only the selected rings are resampled (adjacent selected rings
    form one chain); unselected neighbours are joined with a transition strip.
    whole=True: every ring connected through quad strips is taken in.
    """
    chains = []
    claimed = set()
    selected = extract_selected_rings(bm)
    allowed = None if whole else set(v for verts in selected for v in verts)
    for verts in selected:
        if any(v in claimed for v in verts):
            continue
        chain = build_chain(verts, allowed)
        if chain is None:
            continue
        cv = chain.all_verts()
        if cv & claimed:
            continue
        claimed |= cv
        chains.append(chain)

    for chain in chains:
        for ring in chain.rings:
            ring.fit()
        if fit_mode == FIT_CIRCLE:
            mode = FIT_CIRCLE
        elif fit_mode == FIT_SPLINE:
            mode = FIT_SPLINE
        else:
            mode = FIT_CIRCLE if all(r.circle_ok for r in chain.rings) else FIT_SPLINE
        chain.mode = mode
        for ring in chain.rings:
            ring.set_mode(mode)
    forbidden = set()
    for chain in chains:
        forbidden |= chain.all_verts()
    for chain in chains:
        chain.zips = _find_zip_bands(chain, forbidden)
    return chains


# -----------------------------------------------------------------------------
# Zip bands: a strip of quads and triangles between a resampled ring and an
# untouched neighbour loop (what selected-loops mode leaves behind when the
# counts differ). It is rebuilt from scratch, so going back to matching
# counts gives clean quads again.


class ZipBand:
    def __init__(self, ring, faces, L, pL, forward):
        self.ring = ring
        self.faces = faces    # old faces of the band
        self.L = L            # neighbour loop, sorted by ring parameter
        self.pL = pL          # ring parameter of every neighbour vertex
        self.forward = forward  # old faces run along the ring in increasing parameter


def _ring_param(ring, co):
    """Ring parameter (0..1) of a point, from its angle around the ring's
    center, interpolated between the old vertices (works for non-circular
    rings too, as long as they wind once around the center)."""
    d = co - ring.center
    th = math.atan2(d.dot(ring.v), d.dot(ring.u)) % TAU
    thetas = ring.thetas
    j = bisect_right(thetas, th) - 1
    j = min(max(j, 0), ring.n - 1)
    t0 = thetas[j]
    t1 = thetas[j + 1] if j + 1 < ring.n else TAU
    fr = (th - t0) / (t1 - t0) if t1 - t0 > 1e-15 else 0.0
    p0 = ring.params[j]
    p1 = ring.params[j + 1] if j + 1 < ring.n else 1.0
    return (p0 + fr * (p1 - p0)) % 1.0


def _find_zip_bands(chain, forbidden):
    used = set(f for s in chain.strips for f in s)
    used.update(f for _, faces, _ in chain.transitions for f in faces)
    for cap in chain.caps:
        if cap[0] == 'NGON':
            used.add(cap[2])
        else:
            used.update(cap[3])
    bands = []
    for ring in chain.rings:
        if ring.thetas is None or ring.center is None:
            continue
        vset = ring.vset
        faces = []
        seen = set()
        for v in ring.verts:
            for f in v.link_faces:
                if f not in used and f not in seen:
                    seen.add(f)
                    faces.append(f)
        # Faces on one side of the ring are joined through spoke edges (one
        # end on the ring); the two sides only share ring edges.
        parent = {f: f for f in faces}

        def root(f):
            while parent[f] is not f:
                parent[f] = parent[parent[f]]
                f = parent[f]
            return f

        for f in faces:
            for e in f.edges:
                if (e.verts[0] in vset) != (e.verts[1] in vset):
                    for g in e.link_faces:
                        if g in parent:
                            parent[root(g)] = root(f)
        comps = {}
        for f in faces:
            comps.setdefault(root(f), []).append(f)
        for comp in comps.values():
            zb = _zip_band_from(ring, comp, forbidden)
            if zb is not None:
                bands.append(zb)
    return bands


def _zip_band_from(ring, comp, forbidden):
    vset = ring.vset
    edge_index = {e: k for k, e in enumerate(ring.edges)}
    covered = {}
    L = set()
    l_edges = set()
    forward = None
    for f in comp:
        if len(f.verts) > 4:
            return None
        loops = list(f.loops)
        flags = [l.vert in vset for l in loops]
        if sum(flags[i] != flags[i - 1] for i in range(len(loops))) != 2:
            return None
        for l in loops:
            a, b = l.vert, l.link_loop_next.vert
            if a in vset and b in vset:
                k = edge_index.get(l.edge)
                if k is None:
                    return None
                covered[k] = covered.get(k, 0) + 1
                if forward is None:
                    ia, ib = ring.verts.index(a), ring.verts.index(b)
                    forward = (ib - ia) % ring.n == 1
            elif a not in vset and b not in vset:
                l_edges.add(l.edge)
            if a not in vset:
                L.add(a)
    if forward is None or len(covered) != ring.n or any(c != 1 for c in covered.values()):
        return None
    if len(L) < 3 or L & forbidden or len(l_edges) != len(L):
        return None
    deg = {v: 0 for v in L}
    for e in l_edges:
        for v in e.verts:
            if v not in deg:
                return None
            deg[v] += 1
    if any(d != 2 for d in deg.values()):
        return None
    params = {v: _ring_param(ring, v.co) for v in L}
    order = sorted(L, key=lambda v: params[v])
    # The loop must run around the ring in the same order (no folds).
    for a, b in zip(order, order[1:] + order[:1]):
        if edge_between(a, b) not in l_edges:
            return None
    pL = [params[v] for v in order]
    if len(set(round(p, 9) for p in pL)) != len(pL):
        return None
    return ZipBand(ring, comp, order, pL, forward)


def _align_phase(chain, N):
    """Rotate the new vertices so they line up with untouched neighbour loops
    whose count matches (or divides evenly into) the new count."""
    xs = []
    for ring, faces, nb in chain.transitions:
        xs.extend((p - ring.phase) * N for p in ring.params)
    for zb in chain.zips:
        xs.extend((p - zb.ring.phase) * N for p in zb.pL)
    if not xs:
        return
    c = sum(math.cos(TAU * x) for x in xs) / len(xs)
    s = sum(math.sin(TAU * x) for x in xs) / len(xs)
    if math.hypot(c, s) < 0.95:
        return  # neighbours don't line up with any rotation
    shift = math.atan2(s, c) / TAU / N
    if abs(shift) < 1e-12:
        return
    for ring in chain.rings:
        ring.phase += shift


def _rebuild_zip(bm, zb, N, jobs):
    ring = zb.ring
    W = ring.W
    L, pL = zb.L, zb.pL
    n = len(L)
    p0 = pL[0]
    half = 0.5 / N
    rel = [((ring.new_param(k, N) - p0 + half) % 1.0) - half for k in range(N)]
    order = sorted(range(N), key=lambda k: rel[k])
    ua = [rel[k] for k in order] + [rel[order[0]] + 1.0]
    ub = [(p - p0) % 1.0 for p in pL] + [1.0]
    w_param = {W[k]: ring.new_param(k, N) for k in range(N)}

    def wv(i):
        return W[order[i % N]]

    # Old faces by the middle of their parameter range, for attributes.
    old_mid = []
    for f in zb.faces:
        ps = []
        for v in f.verts:
            ps.append(ring.params[ring.verts.index(v)] if v in ring.vset else pL[L.index(v)])
        c = sum(math.cos(TAU * p) for p in ps)
        s = sum(math.sin(TAU * p) for p in ps)
        old_mid.append(((math.atan2(s, c) / TAU) % 1.0, f))

    def source(mid):
        mid = (mid + p0) % 1.0
        return min(old_mid, key=lambda t: abs(((t[0] - mid + 0.5) % 1.0) - 0.5))[1]

    def emit(kind, i, j):
        if kind == 'Q':
            vs = [wv(i), wv(i + 1), L[(j + 1) % n], L[j % n]]
            mid = (ua[i] + ua[i + 1] + ub[j] + ub[j + 1]) / 4
        elif kind == 'A':
            vs = [wv(i), wv(i + 1), L[j % n]]
            mid = (ua[i] + ua[i + 1] + ub[j]) / 3
        else:
            vs = [wv(i), L[(j + 1) % n], L[j % n]]
            mid = (ua[i] + ub[j] + ub[j + 1]) / 3
        jobs.face(bm, vs if zb.forward else list(reversed(vs)), source(mid))

    i = j = 0
    while i < N or j < n:
        if i == N:
            kind = 'B'
        elif j == n:
            kind = 'A'
        else:
            both = abs(ua[i + 1] - ub[j + 1])
            a = abs(ua[i + 1] - ub[j])
            b = abs(ua[i] - ub[j + 1])
            if both <= min(a, b) + 1e-9:
                kind = 'Q'
            elif a < b:
                kind = 'A'
            else:
                kind = 'B'
        emit(kind, i, j)
        if kind in ('Q', 'A'):
            i += 1
        if kind in ('Q', 'B'):
            j += 1

    # Spoke data: each old spoke goes to the new spoke nearest to it; other
    # new spokes get plain copies.
    vparam = {v: ring.params[k] for k, v in enumerate(ring.verts)}
    for lv in L:
        olds = [e for e in lv.link_edges if e.other_vert(lv) in ring.vset]
        news = [e for e in lv.link_edges if e.other_vert(lv) in ring.W_set]
        if not olds or not news:
            continue
        dist = lambda e_new, p: abs(((w_param[e_new.other_vert(lv)] - p + 0.5) % 1.0) - 0.5)  # noqa: E731
        best = {}
        for s in olds:
            e = min(news, key=lambda e_new: dist(e_new, vparam[s.other_vert(lv)]))
            best.setdefault(e, s)
        for e in news:
            if e in best:
                jobs.edge(e, best[e])
            else:
                s = min(olds, key=lambda s: dist(e, vparam[s.other_vert(lv)]))
                jobs.edge(e, s, seam=False, smooth=True, select=False)


# -----------------------------------------------------------------------------
# Rebuild


class _Jobs:
    """Attribute transfers, applied after all topology exists.

    ``copy_from`` / ``copy_from_*_interp`` invalidate the Python wrapper of the
    element they write to, so the targets are re-fetched by index when the jobs
    run instead of being touched again.
    """

    def __init__(self):
        self.faces = []  # (face, src_face)
        self.edges = []  # (edge, src_edge, seam, smooth, select)
        self.verts = []  # (vert, src_a, src_b, factor, co, select)

    def face(self, bm, verts, src):
        try:
            f = bm.faces.new(verts)
        except ValueError:
            return None
        self.faces.append((f, src))
        return f

    def edge(self, dst, src, seam=None, smooth=None, select=None):
        if dst is None or src is None or dst == src:
            return
        self.edges.append((dst, src, seam, smooth, src.select if select is None else select))

    def run(self, bm):
        for seq in (bm.verts, bm.edges, bm.faces):
            seq.index_update()
            seq.ensure_lookup_table()
        faces = [(f.index, s) for f, s in self.faces]
        edges = [(e.index, s, a, b, c) for e, s, a, b, c in self.edges]
        verts = [(v.index, a, b, fr, co, sel) for v, a, b, fr, co, sel in self.verts]
        self.faces = self.edges = self.verts = None

        sel_f, sel_e, sel_v = [], [], []
        for i, s in faces:
            bm.faces[i].copy_from(s)
            bm.faces[i].copy_from_face_interp(s, False)
            if s.select:
                sel_f.append(i)
        for i, s, seam, smooth, sel in edges:
            bm.edges[i].copy_from(s)
            e = bm.edges[i]
            if seam is not None:
                e.seam = seam
            if smooth is not None:
                e.smooth = smooth
            if sel:
                sel_e.append(i)
        for i, a, b, fr, co, sel in verts:
            bm.verts[i].copy_from_vert_interp((a, b), fr)
            bm.verts[i].co = co
            if sel:
                sel_v.append(i)
        # Fresh wrappers, still valid after the old geometry is deleted.
        return ([bm.verts[i] for i in sel_v],
                [bm.edges[i] for i in sel_e],
                [bm.faces[i] for i in sel_f])


def _get_edge(bm, a, b):
    e = bm.edges.get((a, b))
    if e is None:
        e = bm.edges.new((a, b))
    return e


def _transfer_rails(bm, ring, N, new_pairs, old_edges, jobs):
    """Copy edge data onto the N new rail/spoke edges.

    Every old edge is mapped to the new edge nearest to it, so marks such as a
    UV seam or a sharp edge survive exactly once. New edges with no old edge
    nearby (when adding segments) only get a seam/sharp mark if both old
    neighbours had it.
    """
    n = ring.n
    reps = [[] for _ in range(N)]
    for j in range(n):
        reps[ring.nearest_new(j, N)].append(j)
    for k in range(N):
        a, b = new_pairs[k]
        e = bm.edges.get((a, b))
        if e is None:
            continue
        if reps[k]:
            js = [j for j in reps[k] if old_edges[j] is not None]
            if not js:
                continue
            prim = min(js, key=lambda j: abs((((ring.params[j] - ring.phase) * N - k + N / 2) % N) - N / 2))
            src = [old_edges[j] for j in js]
            jobs.edge(e, old_edges[prim],
                      seam=any(s.seam for s in src),
                      smooth=all(s.smooth for s in src),
                      select=any(s.select for s in src))
        else:
            j, fr = ring.locate(ring.new_param(k, N))
            e0 = old_edges[j]
            e1 = old_edges[(j + 1) % n]
            if e0 is None or e1 is None:
                continue
            jobs.edge(e, e0 if fr < 0.5 else e1,
                      seam=e0.seam and e1.seam,
                      smooth=e0.smooth or e1.smooth,
                      select=e0.select and e1.select)


def _restitch_face(bm, F, ring_lookup, jobs):
    """Rebuild a face that touches resampled rings but is not part of a strip
    or cap: every run of consecutive ring vertices in it is replaced by the
    matching run of new ring vertices."""
    loops = list(F.loops)
    L = len(loops)
    info = [ring_lookup.get(l.vert) for l in loops]

    def link(i):
        a = info[i]
        b = info[(i + 1) % L]
        if a is None or b is None or a[0] is not b[0]:
            return 0
        n = a[0].n
        d = (b[1] - a[1]) % n
        if d == 1:
            return 1
        if d == n - 1:
            return -1
        return 0

    links = [link(i) for i in range(L)]

    start = None
    for i in range(L):
        if links[(i - 1) % L] == 0:
            start = i
            break

    out = []
    prov = []  # old loop index each output vertex comes from (None = interior)

    if start is None:
        # The face is one whole ring (an n-gon cap that wasn't classified).
        ring = info[0][0]
        W = ring.W
        d = links[0]
        k0 = ring.nearest_new(info[0][1], ring.N)
        out = [W[(k0 + d * t) % ring.N] for t in range(ring.N)]
        prov = [None] * len(out)
    else:
        groups = []
        cur = None
        order = [(start + i) % L for i in range(L)]
        for idx, p in enumerate(order):
            inf = info[p]
            if inf is None:
                cur = None
                groups.append(('V', p))
                continue
            if cur is not None:
                lk = links[order[idx - 1]]
                if lk != 0 and (cur[2] == 0 or cur[2] == lk):
                    cur[1].append(p)
                    cur[2] = lk
                    continue
            cur = ['R', [p], 0]
            groups.append(cur)

        for g in groups:
            if g[0] == 'V':
                out.append(loops[g[1]].vert)
                prov.append(g[1])
                continue
            ps, d = g[1], g[2]
            ring = info[ps[0]][0]
            N = ring.N
            ks = ring.nearest_new(info[ps[0]][1], N)
            if len(ps) == 1:
                out.append(ring.W[ks])
                prov.append(ps[0])
                continue
            ke = ring.nearest_new(info[ps[-1]][1], N)
            steps = ((ke - ks) * d) % N
            for t in range(steps + 1):
                out.append(ring.W[(ks + d * t) % N])
                prov.append(ps[0] if t == 0 else (ps[-1] if t == steps else None))
            if steps == 0:
                prov[-1] = None

    # Collapse consecutive duplicates (cyclic).
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
        return None  # degenerate after resampling: dropped

    f = jobs.face(bm, vs, F)
    if f is None:
        return None
    # Edge data for edges between a new ring vertex and the rest of the mesh.
    m = len(vs)
    for i in range(m):
        pa, pb = pv[i], pv[(i + 1) % m]
        if pa is None or pb is None or (pa + 1) % L != pb:
            continue
        jobs.edge(bm.edges.get((vs[i], vs[(i + 1) % m])), loops[pa].edge)
    return f


def _zip_transition(bm, ring, faces, nb, N, jobs):
    """Join the resampled ring (N new vertices) to the untouched neighbour ring
    ``nb`` (n vertices, aligned with the old ring) with quads where the
    vertices line up and triangles where the counts differ."""
    n = ring.n
    W = ring.W
    old = ring.verts
    rails = [edge_between(old[j], nb[j]) for j in range(n)]

    # Unwrapped curve parameters, starting at neighbour vertex 0.
    k0 = ring.nearest_new(0, N)
    d0 = ((ring.new_param(k0, N) + 0.5) % 1.0) - 0.5
    ua = [d0 + i / N for i in range(N + 1)]
    ub = list(ring.params) + [1.0]

    def wv(i):
        return W[(k0 + i) % N]

    def emit(kind, i, j):
        if kind == 'A':
            js, _ = ring.locate(((ua[i] + ua[i + 1]) * 0.5) % 1.0)
        else:
            js = j % n
        src = faces[js]
        fwd = _face_goes(src, old[js], old[(js + 1) % n])
        if kind == 'Q':
            vs = [wv(i), wv(i + 1), nb[(j + 1) % n], nb[j % n]]
        elif kind == 'A':
            vs = [wv(i), wv(i + 1), nb[j % n]]
        else:
            vs = [wv(i), nb[(j + 1) % n], nb[j % n]]
        jobs.face(bm, vs if fwd else list(reversed(vs)), src)

    i = j = 0
    while i < N or j < n:
        if i == N:
            kind = 'B'
        elif j == n:
            kind = 'A'
        else:
            both = abs(ua[i + 1] - ub[j + 1])
            a = abs(ua[i + 1] - ub[j])
            b = abs(ua[i] - ub[j + 1])
            if both <= min(a, b) + 1e-9:
                kind = 'Q'
            elif a < b:
                kind = 'A'
            else:
                kind = 'B'
        emit(kind, i, j)
        if kind in ('Q', 'A'):
            i += 1
        if kind in ('Q', 'B'):
            j += 1

    # Rail data: each old rail goes to the new edge from its neighbour vertex to
    # the nearest new vertex; other connecting edges get plain copies.
    for j in range(n):
        src = rails[j]
        if src is None:
            continue
        best = W[ring.nearest_new(j, N)]
        for e in nb[j].link_edges:
            o = e.other_vert(nb[j])
            if o in ring.W_set:
                if o == best:
                    jobs.edge(e, src)
                else:
                    jobs.edge(e, src, seam=False, smooth=True, select=False)


def resample(bm, chains, counts, force=False, scale=1.0):
    """Resample ``chains`` (from ``analyze`` on this same bm) to ``counts``.

    Chains whose target equals their current count are left untouched unless
    ``force`` is set (useful to re-fit a ring to a perfect circle) or the
    rings are scaled. ``scale`` multiplies every ring's radius (in its own
    plane, about its own center). Returns the number of chains rebuilt.
    """
    import bmesh

    active = []
    in_place = []
    for chain, N in zip(chains, counts):
        if N is None:
            continue
        for ring in chain.rings:
            ring.scale = max(scale, 1e-3)
        N = max(MIN_SEGMENTS, min(MAX_SEGMENTS, int(N)))
        if N == chain.n:
            if force or scale != 1.0:
                in_place.append(chain)
            continue
        active.append((chain, N))

    # Same count: just move the vertices onto the fitted curve. Topology,
    # vertex order and every attribute stay exactly as they are.
    for chain in in_place:
        fix_in_place(chain)
    if not active:
        if in_place:
            bm.normal_update()
        return len(in_place)

    jobs = _Jobs()
    ring_lookup = {}
    handled = set()
    old_verts = []
    old_ring_edges = set()

    for chain, N in active:
        _align_phase(chain, N)
        # -- new ring vertices + ring edges ------------------------------------
        for ring in chain.rings:
            ring.N = N
            n = ring.n
            pts = ring.sample(N)
            W = [bm.verts.new(p) for p in pts]
            ring.W = W
            ring.W_set = set(W)
            for k, w in enumerate(W):
                j, fr = ring.locate(ring.new_param(k, N))
                sel = ring.verts[ring.nearest_old(k, N)].select
                jobs.verts.append((w, ring.verts[j], ring.verts[(j + 1) % n], fr, pts[k], sel))
            for j, v in enumerate(ring.verts):
                ring_lookup[v] = (ring, j)
            old_verts.extend(ring.verts)
            old_ring_edges.update(ring.edges)
            for k in range(N):
                e = _get_edge(bm, W[k], W[(k + 1) % N])
                j, _ = ring.locate(ring.new_param(k, N, 0.5))
                jobs.edge(e, ring.edges[j])

        # -- quad strips -----------------------------------------------------
        m = len(chain.rings)
        for i, strip in enumerate(chain.strips):
            RA = chain.rings[i]
            RB = chain.rings[(i + 1) % m]
            n = RA.n
            for k in range(N):
                j, _ = RA.locate(RA.new_param(k, N, 0.5))
                fo = strip[j]
                k1 = (k + 1) % N
                if _face_goes(fo, RA.verts[j], RA.verts[(j + 1) % n]):
                    vs = [RA.W[k], RA.W[k1], RB.W[k1], RB.W[k]]
                else:
                    vs = [RA.W[k1], RA.W[k], RB.W[k], RB.W[k1]]
                jobs.face(bm, vs, fo)
            handled.update(strip)
            old_rails = [edge_between(RA.verts[j], RB.verts[j]) for j in range(n)]
            pairs = [(RA.W[k], RB.W[k]) for k in range(N)]
            _transfer_rails(bm, RA, N, pairs, old_rails, jobs)

        # -- transitions to unselected rings ------------------------------------
        for ring, faces, nb in chain.transitions:
            _zip_transition(bm, ring, faces, nb, N, jobs)
            handled.update(faces)
        for zb in chain.zips:
            _rebuild_zip(bm, zb, N, jobs)
            handled.update(zb.faces)

        # -- caps ------------------------------------------------------------
        for cap in chain.caps:
            if cap[0] == 'NGON':
                _, ring, fo = cap
                W = ring.W
                vs = W if _face_goes(fo, ring.verts[0], ring.verts[1]) else list(reversed(W))
                jobs.face(bm, vs, fo)
                handled.add(fo)
            else:
                _, ring, apex, tris = cap
                n = ring.n
                W = ring.W
                for k in range(N):
                    j, _ = ring.locate(ring.new_param(k, N, 0.5))
                    t = tris[j]
                    k1 = (k + 1) % N
                    if _face_goes(t, ring.verts[j], ring.verts[(j + 1) % n]):
                        vs = [W[k], W[k1], apex]
                    else:
                        vs = [W[k1], W[k], apex]
                    jobs.face(bm, vs, t)
                handled.update(tris)
                old_spokes = [edge_between(ring.verts[j], apex) for j in range(n)]
                pairs = [(W[k], apex) for k in range(N)]
                _transfer_rails(bm, ring, N, pairs, old_spokes, jobs)

    # -- everything else touching the rings: re-stitch ------------------------
    affected = []
    seen = set()
    for v in old_verts:
        for f in v.link_faces:
            if f in handled or f in seen:
                continue
            seen.add(f)
            affected.append(f)
    for F in affected:
        _restitch_face(bm, F, ring_lookup, jobs)

    # Loose (wire) edges hanging off ring vertices.
    def mapped(v):
        r, j = ring_lookup[v]
        return r.W[r.nearest_new(j, r.N)]

    for v in old_verts:
        for e in v.link_edges:
            if e.link_faces or e in old_ring_edges:
                continue
            o = e.other_vert(v)
            a = mapped(v)
            b = mapped(o) if o in ring_lookup else o
            if a != b:
                jobs.edge(_get_edge(bm, a, b), e)

    # -- copy attributes, then remove the old geometry ---------------------------
    sel_verts, sel_edges, sel_faces = jobs.run(bm)
    bmesh.ops.delete(bm, geom=list(set(old_verts)), context='VERTS')

    # -- selection ---------------------------------------------------------------
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
    return len(active) + len(in_place)


def ring_is_perfect(ring, tol=1e-4):
    """True if the ring already is an evenly spaced circle (within tol * radius)."""
    if ring.center is None or ring.radius <= 0.0:
        return False
    saved = ring.mode, ring.params, ring.phase, ring.scale
    ring.set_mode(FIT_CIRCLE)
    ring.scale = 1.0
    targets = ring.sample(ring.n)
    ring.mode, ring.params, ring.phase, ring.scale = saved
    limit = tol * ring.radius
    return all((p - t).length <= limit for p, t in zip(ring.pts, targets))


def fix_in_place(chain):
    """Snap every ring of the chain onto its fitted curve without changing the
    segment count (old vertex j -> evenly spaced position j)."""
    for ring in chain.rings:
        for v, p in zip(ring.verts, ring.sample(ring.n)):
            v.co = p
