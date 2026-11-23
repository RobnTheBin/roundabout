# SPDX-FileCopyrightText: 2026 Robin Mariančík
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Collapsed curves that can be brought back.

When a curve (typically a bevel) is collapsed with radius 0, every column of
its band is merged into one vertex. The positions of those vertices and the
bevel's size, segment count and profile are remembered here for as long as
the mesh stays in edit mode. Selecting the collapsed edge again and running
the tool then rebuilds the bevel (with Blender's bevel operator), starting
from radius 0.
"""

import bpy
from mathutils import Vector, kdtree

from .core import edge_between
from .curves import PROFILE_MAX, PROFILE_MIN

MATCH_TOLERANCE = 1e-5


class Record:
    def __init__(self, coords, radii, closed, sel, segments, profile):
        self.coords = [Vector(c) for c in coords]  # merged vertex per column (object space)
        self.radii = list(radii)                    # each column's radius before collapsing
        self.closed = closed
        self.sel = sel                              # column the user had selected
        self.radius = radii[sel] if radii[sel] > 0.0 else max(radii)
        self.segments = segments
        self.profile = profile

    def same(self, other):
        return (len(self.coords) == len(other.coords)
                and all((a - b).length < MATCH_TOLERANCE for a, b in zip(self.coords, other.coords)))


_RECORDS = {}  # mesh pointer -> [Record]


def _in_edit_mode(ptr):
    return any(me.as_pointer() == ptr and me.is_editmode for me in bpy.data.meshes)


def purge():
    """Forget collapses of meshes that left edit mode."""
    for ptr in list(_RECORDS):
        if not _in_edit_mode(ptr):
            del _RECORDS[ptr]


def clear():
    _RECORDS.clear()


def on_depsgraph_update(*_args):
    if _RECORDS:
        purge()


def remember(me, records):
    purge()
    lst = _RECORDS.setdefault(me.as_pointer(), [])
    for rec in records:
        lst[:] = [old for old in lst if not old.same(rec)]
        lst.append(rec)


def find(me, bm):
    """[(record, vertex indices)] for remembered collapses still present in
    bm (unchanged) with at least one of their vertices selected."""
    recs = _RECORDS.get(me.as_pointer())
    if not recs or not len(bm.verts):
        return []
    bm.verts.ensure_lookup_table()
    kd = kdtree.KDTree(len(bm.verts))
    for i, v in enumerate(bm.verts):
        kd.insert(v.co, i)
    kd.balance()
    out = []
    for rec in recs:
        idx = []
        for co in rec.coords:
            _, i, dist = kd.find(co)
            if i is None or dist > MATCH_TOLERANCE:
                break
            idx.append(i)
        else:
            if len(set(idx)) != len(idx) or not any(bm.verts[i].select for i in idx):
                continue
            verts = [bm.verts[i] for i in idx]
            if all(edge_between(a, b) is not None for a, b in zip(verts, verts[1:])):
                out.append((rec, idx))
    if out and not _selection_inside(bm, out):
        return []
    return out


def _selection_inside(bm, matches):
    """True when nothing but collapsed edges is selected. A selection that
    also holds other geometry (loops picked to resample or scale) is not a
    request to bring a bevel back."""
    verts = set()
    edges = set()
    for rec, idx in matches:
        vs = [bm.verts[i] for i in idx]
        verts.update(vs)
        pairs = list(zip(vs, vs[1:]))
        if rec.closed and len(vs) > 2:
            pairs.append((vs[-1], vs[0]))
        for a, b in pairs:
            e = edge_between(a, b)
            if e is not None:
                edges.add(e)
    if any(e.select and e not in edges for e in bm.edges):
        return False
    return not any(v.select and v not in verts for v in bm.verts)


def expand(bm, matches, scale, targets, shift):
    """Re-bevel the collapsed edges: offset = original radius * scale."""
    import bmesh

    if scale <= 0.0 or not matches:
        return 0
    bm.verts.ensure_lookup_table()
    jobs = []
    for (rec, idx), M in zip(matches, targets):
        verts = [bm.verts[i] for i in idx]
        n = len(verts)
        pairs = [(i, i + 1) for i in range(n - 1)]
        if rec.closed and n > 2:
            pairs.append((n - 1, 0))
        # Only the edge the bevel itself collapsed into: columns of a much
        # smaller radius (a small bevel wrapping around its end) stay sharp.
        lo, hi = 0.75 * rec.radius, 1.34 * rec.radius
        edges = [edge_between(verts[a], verts[b]) for a, b in pairs
                 if lo <= rec.radii[a] <= hi and lo <= rec.radii[b] <= hi]
        edges = [e for e in edges if e is not None]
        if edges:
            jobs.append((rec, edges, max(1, int(M))))

    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    for f in bm.faces:
        f.select = False

    done = 0
    for rec, edges, M in jobs:
        if not all(e.is_valid for e in edges):
            continue
        offset = rec.radius * scale
        profile = min(max(rec.profile + shift, PROFILE_MIN), PROFILE_MAX)
        res = bmesh.ops.bevel(bm, geom=edges, offset=offset, offset_type='OFFSET',
                              profile_type='SUPERELLIPSE', segments=M, profile=profile,
                              affect='EDGES', clamp_overlap=False)
        _select_column(bm, rec, res.get("faces", []), offset)
        done += 1
    bm.select_flush_mode()
    bm.normal_update()
    return done


def _select_column(bm, rec, faces, offset):
    """Select the rebuilt profile at the column the user had selected."""
    n = len(rec.coords)
    V = rec.coords[rec.sel]
    a = rec.coords[rec.sel - 1] if (rec.sel > 0 or rec.closed) else V
    b = rec.coords[(rec.sel + 1) % n] if (rec.sel < n - 1 or rec.closed) else V
    t = b - a
    if t.length < 1e-12:
        return
    t.normalize()
    tol = 0.02 * offset + 1e-6
    near = set()
    for f in faces:
        for v in f.verts:
            d = v.co - V
            if abs(d.dot(t)) < tol and d.length < 3.0 * offset + 1e-6:
                near.add(v)
    for f in faces:
        for e in f.edges:
            if e.verts[0] in near and e.verts[1] in near:
                e.select = True
                e.verts[0].select = e.verts[1].select = True
