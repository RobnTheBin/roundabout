"""Headless tests. Run with:

    blender -b --factory-startup --python tests/run_tests.py
"""

import math
import os
import sys
import traceback

import bpy
import bmesh
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import roundabout  # noqa: E402
from roundabout import core  # noqa: E402

roundabout.register()

RESULTS = []


# -----------------------------------------------------------------------------
# helpers

def reset():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)


def new_object_from_bm(bm, name="Test"):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob


def edit(ob, mode='EDGE'):
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.context.tool_settings.mesh_select_mode = (mode == 'VERT', mode == 'EDGE', mode == 'FACE')
    bm = bmesh.from_edit_mesh(ob.data)
    bm.select_mode = {mode}
    for el in (*bm.verts, *bm.edges, *bm.faces):
        el.select = False
    return bm


def select_edges(bm, pred):
    for e in bm.edges:
        if pred(e):
            e.select = True
    bm.select_flush_mode()


def ring_pred(z, r=None, tol=1e-4):
    def p(e):
        a, b = e.verts
        if abs(a.co.z - z) > tol or abs(b.co.z - z) > tol:
            return False
        if r is not None:
            for v in (a, b):
                if abs(v.co.xy.length - r) > 1e-3:
                    return False
        return True
    return p


def check_topology(bm, expect_boundary=0, closed_orientation=True):
    bm.verts.ensure_lookup_table()
    problems = []
    nonman = [e for e in bm.edges if len(e.link_faces) > 2]
    if nonman:
        problems.append(f"{len(nonman)} non-manifold edges")
    boundary = [e for e in bm.edges if len(e.link_faces) == 1]
    if expect_boundary is not None and len(boundary) != expect_boundary:
        problems.append(f"boundary edges {len(boundary)} != {expect_boundary}")
    for f in bm.faces:
        if f.calc_area() < 1e-9:
            problems.append("degenerate face")
            break
    keys = [frozenset(v.index for v in f.verts) for f in bm.faces]
    if len(keys) != len(set(keys)):
        problems.append("duplicate faces")
    loose = [v for v in bm.verts if not v.link_edges]
    if loose:
        problems.append(f"{len(loose)} loose verts")
    if closed_orientation:
        bad = 0
        for e in bm.edges:
            if len(e.link_faces) == 2:
                f1, f2 = e.link_faces
                a, b = e.verts
                if core._face_goes(f1, a, b) == core._face_goes(f2, a, b):
                    bad += 1
        if bad:
            problems.append(f"{bad} edges with inconsistent winding")
    return problems


def run_op(**kw):
    kw.setdefault("whole_shape", True)
    return bpy.ops.mesh.roundabout(**kw)


def analyze(bm, fit_mode=core.FIT_AUTO, whole=True):
    return core.analyze(bm, fit_mode, whole=whole)


def test(fn):
    name = fn.__name__
    try:
        reset()
        fn()
        RESULTS.append((name, True, ""))
    except Exception as ex:  # noqa: BLE001
        RESULTS.append((name, False, f"{ex}\n{traceback.format_exc()}"))
    return fn


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def counts(ob):
    bm = bmesh.from_edit_mesh(ob.data)
    return len(bm.verts), len(bm.edges), len(bm.faces)


# -----------------------------------------------------------------------------
# tests

@test
def plain_cylinder_ngon():
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=1, depth=2, end_fill_type='NGON')
    ob = bpy.context.object
    bm = edit(ob)
    select_edges(bm, ring_pred(1.0))
    bmesh.update_edit_mesh(ob.data)
    expect(run_op(delta=8) == {'FINISHED'}, "op failed")
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 48, f"verts {len(bm.verts)}")
    expect(len(bm.faces) == 26, f"faces {len(bm.faces)}")
    expect(not check_topology(bm), check_topology(bm))
    for v in bm.verts:
        expect(abs(v.co.xy.length - 1.0) < 1e-5, "vertex off circle")
    sel = [e for e in bm.edges if e.select]
    expect(len(sel) == 24, f"selected edges {len(sel)}")
    # again, downwards, from the result
    expect(run_op(delta=-18) == {'FINISHED'}, "op2 failed")
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 12, f"verts {len(bm.verts)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def trifan_cylinder():
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, end_fill_type='TRIFAN')
    ob = bpy.context.object
    bm = edit(ob)
    select_edges(bm, ring_pred(-1.0))
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=20)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 64 + 2, f"verts {len(bm.verts)}")
    expect(len(bm.faces) == 32 * 3, f"faces {len(bm.faces)}")
    expect(all(len(f.verts) in (3, 4) for f in bm.faces), "fan not rebuilt as triangles")
    expect(not check_topology(bm), check_topology(bm))


def make_beveled_cylinder(segs=16, bevel_segments=3):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs,
                          radius1=1.0, radius2=1.0, depth=2.0)
    cap_edges = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) < 1e-6]
    bmesh.ops.bevel(bm, geom=cap_edges, offset=0.2, segments=bevel_segments,
                    profile=0.5, affect='EDGES')
    return new_object_from_bm(bm, "Beveled")


@test
def beveled_cylinder():
    ob = make_beveled_cylinder(16, 3)
    bm = edit(ob)
    select_edges(bm, ring_pred(0.0) if any(abs(v.co.z) < 1e-6 for v in bm.verts) else ring_pred(-0.8))
    if not any(e.select for e in bm.edges):
        # no middle ring: pick the side ring at the bevel start
        zs = sorted({round(v.co.z, 5) for v in bm.verts})
        select_edges(bm, ring_pred(zs[len(zs) // 2]))
    bmesh.update_edit_mesh(ob.data)
    v0, _, f0 = counts(ob)
    chains = analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(chains) == 1, f"chains {len(chains)}")
    rings = len(chains[0].rings)
    expect(rings == v0 // 16, f"chain should hold all {v0 // 16} rings, got {rings}")
    expect(len(chains[0].caps) == 2, f"caps {len(chains[0].caps)}")
    expect(chains[0].mode == core.FIT_CIRCLE, "expected circle fit")
    run_op(delta=16)
    v1, _, f1 = counts(ob)
    expect(v1 == rings * 32, f"verts {v1} != {rings * 32}")
    bm = bmesh.from_edit_mesh(ob.data)
    expect(not check_topology(bm), check_topology(bm))
    run_op(delta=-24)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == rings * 8, f"verts {len(bm.verts)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def pipe_closed_chain():
    # Tube with wall thickness: outer and inner wall + annular caps = closed chain.
    bm = bmesh.new()
    n = 12
    def ring(r, z):
        return [bm.verts.new((r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), z))
                for k in range(n)]
    rs = [ring(1, -1), ring(1, 1), ring(0.7, 1), ring(0.7, -1)]
    for i in range(4):
        A, B = rs[i], rs[(i + 1) % 4]
        for k in range(n):
            bm.faces.new((A[k], A[(k + 1) % n], B[(k + 1) % n], B[k]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    ob = new_object_from_bm(bm, "Pipe")
    bm = edit(ob)
    select_edges(bm, ring_pred(1.0, r=1.0))
    bmesh.update_edit_mesh(ob.data)
    chains = analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(chains) == 1 and chains[0].closed, "expected closed chain")
    expect(len(chains[0].rings) == 4, "expected 4 rings")
    run_op(delta=-4)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 32 and len(bm.faces) == 32, f"{len(bm.verts)} {len(bm.faces)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def torus_minor_and_major():
    bpy.ops.mesh.primitive_torus_add(major_segments=24, minor_segments=8)
    ob = bpy.context.object
    bm = edit(ob)
    # a minor ring: the 8 verts whose angle around Z is 0
    bm.verts.ensure_lookup_table()
    def on_plane(v):
        return abs(v.co.y) < 1e-5 and v.co.x > 0
    select_edges(bm, lambda e: all(on_plane(v) for v in e.verts))
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=8)  # minor 8 -> 16
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 24 * 16, f"verts {len(bm.verts)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def uv_sphere_fans():
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    ob = bpy.context.object
    bm = edit(ob)
    select_edges(bm, ring_pred(0.0))
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=-8)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 7 * 8 + 2, f"verts {len(bm.verts)}")
    expect(not check_topology(bm), check_topology(bm))


def make_boss_on_plate(n=16):
    """A cylinder standing on a square plate, merged into the plate's n-gons."""
    bm = bmesh.new()
    bot = [bm.verts.new((math.cos(2 * math.pi * k / n), math.sin(2 * math.pi * k / n), 0)) for k in range(n)]
    top = [bm.verts.new((math.cos(2 * math.pi * k / n), math.sin(2 * math.pi * k / n), 1)) for k in range(n)]
    corners = [bm.verts.new((3 * math.cos(math.pi / 4 + s * math.pi / 2),
                             3 * math.sin(math.pi / 4 + s * math.pi / 2), 0)) for s in range(4)]
    for k in range(n):
        bm.faces.new((bot[k], bot[(k + 1) % n], top[(k + 1) % n], top[k]))
    bm.faces.new(top)
    q = n // 4
    for s in range(4):
        ks = [(n // 8 + q * s + i) % n for i in range(q + 1)]  # ring verts between corner s and s+1
        bm.faces.new([corners[(s + 1) % 4], corners[s]] + [bot[k] for k in ks])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return new_object_from_bm(bm, "Boss")


@test
def boss_merged_into_plate():
    n = 16
    for delta in (8, -8, -11, 3, 1):
        reset()
        ob = make_boss_on_plate(n)
        bm = edit(ob)
        select_edges(bm, ring_pred(1.0))
        bmesh.update_edit_mesh(ob.data)
        chains = analyze(bmesh.from_edit_mesh(ob.data))
        expect(len(chains) == 1 and len(chains[0].rings) == 2, "expected 2-ring chain")
        expect(run_op(delta=delta) == {'FINISHED'}, "op failed")
        bm = bmesh.from_edit_mesh(ob.data)
        probs = check_topology(bm, expect_boundary=4)
        expect(not probs, f"delta {delta}: {probs}")
        top = [v for v in bm.verts if abs(v.co.z - 1) < 1e-6]
        expect(len(top) == n + delta, f"top ring count {len(top)}")
        plate = [f for f in bm.faces if all(abs(v.co.z) < 1e-6 for v in f.verts)]
        expect(len(plate) == 4, f"plate faces {len(plate)}")


@test
def vertex_mode_ladder_selection():
    bpy.ops.mesh.primitive_cylinder_add(vertices=10)
    ob = bpy.context.object
    bm = edit(ob, 'VERT')
    for v in bm.verts:
        v.select = True  # everything, including caps
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    chains = analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(chains) == 1 and len(chains[0].rings) == 2, f"chains {[len(c.rings) for c in chains]}")
    run_op(delta=6)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 32, f"verts {len(bm.verts)}")
    expect(all(v.select for v in bm.verts), "selection not kept")
    expect(not check_topology(bm), check_topology(bm))


@test
def face_mode_side_selection():
    bpy.ops.mesh.primitive_cylinder_add(vertices=10)
    ob = bpy.context.object
    bm = edit(ob, 'FACE')
    for f in bm.faces:
        if len(f.verts) == 4:
            f.select = True
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=-2)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 16, f"verts {len(bm.verts)}")
    expect(sum(f.select for f in bm.faces) == 8, "side faces should stay selected")
    expect(not check_topology(bm), check_topology(bm))


@test
def wire_circle():
    bpy.ops.mesh.primitive_circle_add(vertices=8, fill_type='NOTHING')
    ob = bpy.context.object
    bm = edit(ob)
    for e in bm.edges:
        e.select = True
    bmesh.update_edit_mesh(ob.data)
    run_op(use_absolute=True, segments=40)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 40 and len(bm.edges) == 40, f"{len(bm.verts)} {len(bm.edges)}")


@test
def elliptical_keeps_profile():
    bpy.ops.mesh.primitive_cylinder_add(vertices=16)
    ob = bpy.context.object
    bm = edit(ob)
    for v in bm.verts:
        v.co.x *= 2.0
    select_edges(bm, ring_pred(1.0))
    bmesh.update_edit_mesh(ob.data)
    chains = analyze(bmesh.from_edit_mesh(ob.data))
    expect(chains[0].mode == core.FIT_SPLINE, "ellipse should use profile fit")
    run_op(delta=16)
    bm = bmesh.from_edit_mesh(ob.data)
    xs = [v.co.x for v in bm.verts]
    expect(max(xs) > 1.95 and max(xs) < 2.05, f"ellipse width lost {max(xs)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def seam_and_uv_preserved():
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, calc_uvs=True)
    ob = bpy.context.object
    bm = edit(ob)
    # mark one rail as seam
    rail = next(e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1.5)
    rail.seam = True
    for e in bm.edges:
        if abs(e.verts[0].co.z - 1) < 1e-6 and abs(e.verts[1].co.z - 1) < 1e-6:
            e.seam = True
    select_edges(bm, ring_pred(1.0))
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=8)
    bm = bmesh.from_edit_mesh(ob.data)
    rails = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1.5]
    expect(sum(e.seam for e in rails) == 1, f"rail seams {sum(e.seam for e in rails)}")
    top = [e for e in bm.edges if abs(e.verts[0].co.z - 1) < 1e-6 and abs(e.verts[1].co.z - 1) < 1e-6]
    expect(all(e.seam for e in top) and len(top) == 24, "ring seams lost")
    uv = bm.loops.layers.uv.active
    expect(uv is not None, "uv layer lost")
    for f in bm.faces:
        for l in f.loops:
            x, y = l[uv].uv
            expect(-0.05 <= x <= 1.05 and -0.05 <= y <= 1.05, f"uv out of range {x, y}")


@test
def two_cylinders_relative_delta():
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=1, radius2=1, depth=2)
    m = bmesh.ops.create_cone(bm, cap_ends=True, segments=20, radius1=1, radius2=1, depth=2)
    bmesh.ops.translate(bm, verts=m['verts'], vec=(5, 0, 0))
    ob = new_object_from_bm(bm, "Two")
    bm = edit(ob)
    select_edges(bm, ring_pred(1.0))
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=4)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 24 + 48, f"verts {len(bm.verts)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def snapshot_restore_roundtrip():
    """Mimics the modal operator: snapshot, then repeated restore + resample."""
    ob = make_beveled_cylinder(16, 2)
    bm = edit(ob)
    zs = sorted({round(v.co.z, 5) for v in bm.verts})
    select_edges(bm, ring_pred(zs[len(zs) // 2]))
    bmesh.update_edit_mesh(ob.data)
    me = ob.data
    bm = bmesh.from_edit_mesh(me)
    orig_co = sorted((round(v.co.x, 5), round(v.co.y, 5), round(v.co.z, 5)) for v in bm.verts)
    orig = [c.n for c in analyze(bm)]
    snap = bpy.data.meshes.new("snap")
    bm.to_mesh(snap)
    for target in (17, 30, 5, 64, 16):
        bm = bmesh.from_edit_mesh(me)
        sm = set(bm.select_mode)
        bm.clear()
        bm.from_mesh(snap)
        bm.select_mode = sm
        chains = analyze(bm)
        expect([c.n for c in chains] == orig, "re-analysis mismatch")
        core.resample(bm, chains, [target])
        bmesh.update_edit_mesh(me)
        bm = bmesh.from_edit_mesh(me)
        expect(not check_topology(bm), f"{target}: {check_topology(bm)}")
    now = sorted((round(v.co.x, 5), round(v.co.y, 5), round(v.co.z, 5)) for v in bm.verts)
    expect(len(now) == len(orig_co), f"vert count {len(now)} != {len(orig_co)}")
    diff = [(a, b) for a, b in zip(now, orig_co) if a != b]
    expect(not diff, f"back at the original count the mesh should be identical: {diff[:3]}")
    bpy.data.meshes.remove(snap)
    bpy.ops.object.mode_set(mode='OBJECT')


@test
def nothing_selected_reports_error():
    bpy.ops.mesh.primitive_cylinder_add(vertices=8)
    ob = bpy.context.object
    edit(ob)
    bmesh.update_edit_mesh(ob.data)
    try:
        res = run_op(delta=2)
    except RuntimeError:
        res = {'CANCELLED'}
    expect(res == {'CANCELLED'}, f"expected cancel, got {res}")


@test
def cone_fan_tip_and_ngon_base():
    bpy.ops.mesh.primitive_cone_add(vertices=8, end_fill_type='NGON')
    ob = bpy.context.object
    bm = edit(ob)
    select_edges(bm, ring_pred(-1.0))
    bmesh.update_edit_mesh(ob.data)
    chains = analyze(bmesh.from_edit_mesh(ob.data))
    expect(sorted(c[0] for c in chains[0].caps) == ['FAN', 'NGON'], f"caps {[c[0] for c in chains[0].caps]}")
    run_op(delta=24)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 33 and len(bm.faces) == 33, f"{len(bm.verts)} {len(bm.faces)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def materials_and_smooth_kept():
    bpy.ops.mesh.primitive_cylinder_add(vertices=12)
    ob = bpy.context.object
    for name in ('side', 'cap'):
        ob.data.materials.append(bpy.data.materials.new(name))
    bm = edit(ob)
    for f in bm.faces:
        f.material_index = 1 if len(f.verts) > 4 else 0
        f.smooth = len(f.verts) == 4
    select_edges(bm, ring_pred(1.0))
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=12)
    bm = bmesh.from_edit_mesh(ob.data)
    for f in bm.faces:
        cap = len(f.verts) > 4
        expect(f.material_index == (1 if cap else 0), "material lost")
        expect(f.smooth == (not cap), "smooth flag lost")


@test
def timing_heavy_mesh():
    import time
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=300, y_subdivisions=300, size=20)
    grid = bpy.context.object
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, location=(0, 0, 2))
    cyl = bpy.context.object
    grid.select_set(True)
    cyl.select_set(True)
    bpy.context.view_layer.objects.active = cyl
    bpy.ops.object.join()
    ob = bpy.context.object
    bm = edit(ob)
    select_edges(bm, ring_pred(3.0))
    bmesh.update_edit_mesh(ob.data)
    me = ob.data
    bm = bmesh.from_edit_mesh(me)
    snap = bpy.data.meshes.new("snap")
    bm.to_mesh(snap)
    t0 = time.perf_counter()
    reps = 5
    for i in range(reps):
        bm = bmesh.from_edit_mesh(me)
        sm = set(bm.select_mode)
        bm.clear()
        bm.from_mesh(snap)
        bm.select_mode = sm
        chains = analyze(bm)
        core.resample(bm, chains, [40 + i])
        bmesh.update_edit_mesh(me)
    dt = (time.perf_counter() - t0) / reps
    print(f"    [timing] {len(bm.verts)} verts: {dt * 1000:.0f} ms per wheel step")
    bpy.data.meshes.remove(snap)


def jitter(bm, amount=0.03, seed=1):
    """Deterministically push every vertex off its circle (radius, angle, height)."""
    import random
    rnd = random.Random(seed)
    for v in bm.verts:
        r = v.co.xy.length
        if r < 1e-6:
            continue
        a = math.atan2(v.co.y, v.co.x) + rnd.uniform(-amount, amount) * 0.5
        r *= 1.0 + rnd.uniform(-amount, amount)
        v.co.x, v.co.y = r * math.cos(a), r * math.sin(a)
        v.co.z += rnd.uniform(-amount, amount) * 0.2


def all_rings_perfect(ob):
    chains = analyze(bmesh.from_edit_mesh(ob.data), core.FIT_CIRCLE)
    rings = [r for c in chains for r in c.rings]
    return sum(core.ring_is_perfect(r) for r in rings), len(rings)


@test
def fix_distorted_beveled_cylinder_in_place():
    ob = make_beveled_cylinder(24, 2)
    bm = edit(ob)
    jitter(bm)
    # Select the side ring nearest the middle (heights are jittered, so use a band).
    mid = min(bm.verts, key=lambda v: abs(v.co.z) + abs(v.co.xy.length - 1.0))
    h = mid.co.z
    for e in bm.edges:
        if all(abs(v.co.z - h) < 0.05 and v.co.xy.length > 0.9 for v in e.verts):
            e.select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    before = [v.co.copy() for v in bmesh.from_edit_mesh(ob.data).verts]
    good, total = all_rings_perfect(ob)
    expect(good == 0 and total > 2, f"setup: {good}/{total} perfect")

    expect(run_op(fit_mode='CIRCLE') == {'FINISHED'}, "fix failed")
    bm = bmesh.from_edit_mesh(ob.data)
    after = [v.co.copy() for v in bm.verts]
    expect(len(after) == len(before), "vertex count changed")
    moved = [(a - b).length for a, b in zip(after, before)]
    expect(max(moved) < 0.06, f"vertices moved too far: {max(moved):.4f}")
    good, total = all_rings_perfect(ob)
    expect(good == total, f"only {good}/{total} rings perfect after fix")
    expect(not check_topology(bm), check_topology(bm))
    # Round rings should stay close to the true axis.
    chains = analyze(bm, core.FIT_CIRCLE)
    for r in chains[0].rings:
        expect(r.center.xy.length < 0.02, f"ring center drifted {r.center.xy.length:.4f}")


@test
def fix_leaves_perfect_rings_alone():
    bpy.ops.mesh.primitive_cylinder_add(vertices=20, rotation=(0.3, 0.2, 0.123))
    ob = bpy.context.object
    bm = edit(ob)
    # world rotation does not matter; edit-space ring at local z=1: select by vertex index halves
    bm.verts.ensure_lookup_table()
    for e in bm.edges:
        a, b = e.verts
        if a.index % 2 == 1 and b.index % 2 == 1:
            e.select = True
    bmesh.update_edit_mesh(ob.data)
    before = [v.co.copy() for v in bmesh.from_edit_mesh(ob.data).verts]
    run_op(fit_mode='CIRCLE')
    after = [v.co.copy() for v in bmesh.from_edit_mesh(ob.data).verts]
    worst = max((a - b).length for a, b in zip(after, before))
    expect(worst < 1e-5, f"perfect cylinder moved by {worst}")


@test
def fix_tilted_ring_phase_and_plane():
    # A ring that is rotated around its own axis and tilted must not spin or flip.
    bm = bmesh.new()
    n = 12
    from mathutils import Matrix
    rot = Matrix.Rotation(0.4, 3, 'X') @ Matrix.Rotation(0.17, 3, 'Z')
    for z in (-1, 1):
        for k in range(n):
            a = 2 * math.pi * k / n
            bm.verts.new(rot @ Vector((math.cos(a), math.sin(a), z)))
    bm.verts.ensure_lookup_table()
    for k in range(n):
        bm.faces.new((bm.verts[k], bm.verts[(k + 1) % n], bm.verts[n + (k + 1) % n], bm.verts[n + k]))
    bm.verts[3].co += Vector((0.05, -0.04, 0.03))  # one dented vertex
    ob = new_object_from_bm(bm, "Tilted")
    bm = edit(ob)
    bm.verts.ensure_lookup_table()
    for e in bm.edges:
        if all(v.index < n for v in e.verts):
            e.select = True
    bmesh.update_edit_mesh(ob.data)
    before = [v.co.copy() for v in bmesh.from_edit_mesh(ob.data).verts]
    run_op(fit_mode='CIRCLE')
    bm = bmesh.from_edit_mesh(ob.data)
    moved = [(v.co - b).length for v, b in zip(bm.verts, before)]
    expect(moved[3] > 0.03, "dented vertex not fixed")
    others = max(m for i, m in enumerate(moved) if i != 3)
    expect(others < 0.02, f"undamaged vertices moved {others:.4f}")
    good, total = all_rings_perfect(ob)
    expect(good == total, f"{good}/{total} perfect")


@test
def fix_while_resampling():
    bpy.ops.mesh.primitive_cylinder_add(vertices=16)
    ob = bpy.context.object
    bm = edit(ob)
    jitter(bm, 0.04, seed=7)
    for e in bm.edges:
        if all(v.co.z > 0.5 for v in e.verts):
            e.select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    run_op(fit_mode='CIRCLE', delta=8)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 48, f"verts {len(bm.verts)}")
    good, total = all_rings_perfect(ob)
    expect(good == total == 2, f"{good}/{total} perfect")
    expect(not check_topology(bm), check_topology(bm))


ZS = (-1.0, -0.5, 0.0, 0.5, 1.0)


def make_stacked_cylinder(n=16, zs=ZS, uvs=True):
    """Cylinder with several loop cuts and n-gon caps."""
    bpy.ops.mesh.primitive_cylinder_add(vertices=n, depth=2, calc_uvs=uvs)
    ob = bpy.context.object
    bm = edit(ob)
    rails = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1.5]
    bmesh.ops.subdivide_edges(bm, edges=rails, cuts=len(zs) - 2, use_grid_fill=True)
    for e in bm.edges:
        e.select = False
    for v in bm.verts:
        v.select = False
    bmesh.update_edit_mesh(ob.data)
    return ob


def ring_counts(bm, zs=ZS):
    return [sum(1 for v in bm.verts if abs(v.co.z - z) < 1e-4) for z in zs]


def run_selected(**kw):
    """Run with the operator's own default scope (selected loops only)."""
    return bpy.ops.mesh.roundabout(**kw)


@test
def selected_ring_only_middle():
    for delta in (8, -8, -5, 3, 16, -13):
        reset()
        ob = make_stacked_cylinder()
        bm = bmesh.from_edit_mesh(ob.data)
        select_edges(bm, ring_pred(0.0))
        bmesh.update_edit_mesh(ob.data)
        expect(run_selected(delta=delta) == {'FINISHED'}, "op failed")
        bm = bmesh.from_edit_mesh(ob.data)
        got = ring_counts(bm)
        expect(got == [16, 16, 16 + delta, 16, 16], f"delta {delta}: ring counts {got}")
        probs = check_topology(bm)
        expect(not probs, f"delta {delta}: {probs}")
        expect(all(len(f.verts) in (3, 4, 16) for f in bm.faces), "unexpected face sizes")
        caps = [f for f in bm.faces if len(f.verts) == 16]
        expect(len(caps) == 2, "caps changed")
        sel = sum(e.select for e in bm.edges)
        expect(sel == 16 + delta, f"delta {delta}: {sel} edges selected")


@test
def selected_cap_ring_rebuilds_cap_only():
    ob = make_stacked_cylinder()
    bm = bmesh.from_edit_mesh(ob.data)
    select_edges(bm, ring_pred(1.0))
    bmesh.update_edit_mesh(ob.data)
    run_selected(delta=8)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(ring_counts(bm) == [16, 16, 16, 16, 24], f"{ring_counts(bm)}")
    expect(sorted(len(f.verts) for f in bm.faces if len(f.verts) > 4) == [16, 24], "cap sizes")
    expect(not check_topology(bm), check_topology(bm))


@test
def selected_two_separate_rings():
    ob = make_stacked_cylinder()
    bm = bmesh.from_edit_mesh(ob.data)
    select_edges(bm, ring_pred(-0.5))
    select_edges(bm, ring_pred(0.5))
    bmesh.update_edit_mesh(ob.data)
    chains = core.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(chains) == 2, f"chains {len(chains)}")
    run_selected(delta=-4)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(ring_counts(bm) == [16, 12, 16, 12, 16], f"{ring_counts(bm)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def selected_adjacent_rings_keep_quads_between():
    ob = make_stacked_cylinder()
    bm = bmesh.from_edit_mesh(ob.data)
    select_edges(bm, ring_pred(0.0))
    select_edges(bm, ring_pred(0.5))
    bmesh.update_edit_mesh(ob.data)
    run_selected(delta=8)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(ring_counts(bm) == [16, 16, 24, 24, 16], f"{ring_counts(bm)}")
    between = [f for f in bm.faces
               if all(abs(v.co.z) < 1e-4 or abs(v.co.z - 0.5) < 1e-4 for v in f.verts)]
    expect(len(between) == 24 and all(len(f.verts) == 4 for f in between), "band between selected rings")
    expect(not check_topology(bm), check_topology(bm))


@test
def selected_transition_keeps_seam_path():
    ob = make_stacked_cylinder()
    bm = bmesh.from_edit_mesh(ob.data)
    # the UV seam runs along one column of rails
    col = min(bm.verts, key=lambda v: (abs(v.co.z), -v.co.x))  # a ring vertex at z=0
    x, y = col.co.x, col.co.y
    for e in bm.edges:
        if all(abs(v.co.x - x) < 1e-4 and abs(v.co.y - y) < 1e-4 for v in e.verts):
            e.seam = True
    select_edges(bm, ring_pred(0.0))
    bmesh.update_edit_mesh(ob.data)
    run_selected(delta=8)
    bm = bmesh.from_edit_mesh(ob.data)
    seams = [e for e in bm.edges if e.seam and abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-4]
    expect(len(seams) == 4, f"{len(seams)} seam edges along the side (expected 4)")
    heights = sorted(round(min(v.co.z for v in e.verts), 3) for e in seams)
    expect(heights == [-1.0, -0.5, 0.0, 0.5], f"seam path broken {heights}")
    uv = bm.loops.layers.uv.active
    for f in bm.faces:
        for l in f.loops:
            u, w = l[uv].uv
            expect(-0.05 <= u <= 1.05 and -0.05 <= w <= 1.05, f"uv out of range {u, w}")


@test
def selected_fix_only_touches_selected_ring():
    ob = make_stacked_cylinder()
    bm = bmesh.from_edit_mesh(ob.data)
    jitter(bm, 0.03, seed=3)
    select_edges(bm, lambda e: all(abs(v.co.z) < 0.05 for v in e.verts))
    bmesh.update_edit_mesh(ob.data)
    before = [v.co.copy() for v in bmesh.from_edit_mesh(ob.data).verts]
    run_selected(fit_mode='CIRCLE')
    bm = bmesh.from_edit_mesh(ob.data)
    moved = [i for i, (v, b) in enumerate(zip(bm.verts, before)) if (v.co - b).length > 1e-7]
    expect(moved and all(abs(before[i].z) < 0.05 for i in moved), "vertices outside the selected ring moved")
    chains = core.analyze(bm, core.FIT_CIRCLE)
    expect(all(core.ring_is_perfect(r) for c in chains for r in c.rings), "selected ring not perfect")


# -----------------------------------------------------------------------------
# open curves (bevel profiles) and radius

from roundabout import curves  # noqa: E402

TAU = 2.0 * math.pi


def make_profile_cylinder(profile, n=16, name="Profile"):
    """Revolve (r, z) points around Z, with n-gon caps at both ends."""
    bm = bmesh.new()
    rings = []
    for r, z in profile:
        rings.append([bm.verts.new((r * math.cos(TAU * k / n), r * math.sin(TAU * k / n), z))
                      for k in range(n)])
    for A, B in zip(rings, rings[1:]):
        for k in range(n):
            k1 = (k + 1) % n
            bm.faces.new((A[k], A[k1], B[k1], B[k]))
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    bm.normal_update()
    return new_object_from_bm(bm, name)


def arc(w=0.3, angles=(0, 30, 60, 90), top=True):
    c = 1.0 - w
    pts = [(c + w * math.cos(math.radians(a)), c + w * math.sin(math.radians(a))) for a in angles]
    return pts if top else [(r, -z) for r, z in reversed(pts)]


def select_column(bm, zmin, zmax=10.0, direction=(1.0, 0.0)):
    dx, dy = direction

    def on(v):
        return (abs(v.co.x * dy - v.co.y * dx) < 1e-4 and v.co.x * dx + v.co.y * dy > 0
                and zmin - 1e-6 <= v.co.z <= zmax + 1e-6)
    select_edges(bm, lambda e: on(e.verts[0]) and on(e.verts[1]))


def column_angles(bm, w=0.3):
    c = 1.0 - w
    out = []
    for v in bm.verts:
        if abs(v.co.y) < 1e-6 and v.co.x > 0 and v.co.z >= c - 1e-6 and v.co.x >= c - 1e-6:
            out.append(math.degrees(math.atan2(v.co.z - c, v.co.x - c)))
    return sorted(out)


def close_to(got, want, tol=1e-3):
    return len(got) == len(want) and all(abs(a - e) < tol for a, e in zip(got, want))


def on_bevel_arc(bm, w=0.3):
    c = 1.0 - w
    worst = 0.0
    for v in bm.verts:
        r = v.co.xy.length
        if v.co.z > c + 1e-6 and r > c + 1e-6:
            worst = max(worst, abs(math.hypot(r - c, v.co.z - c) - w))
    return worst


def ring_count_z(bm, z):
    return sum(1 for v in bm.verts if abs(v.co.z - z) < 1e-5)


def flatness(bm):
    return max(abs(v.co.xy.length + v.co.z - 1.7) for v in bm.verts
               if v.co.z > 0.7 + 1e-6 and v.co.xy.length > 0.7 + 1e-6)


@test
def curve_bevel_profile_resample():
    ob = make_profile_cylinder([(1.0, -1.0)] + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1, f"bands {len(bands)}")
    b = bands[0]
    expect(b.closed and len(b.columns) == 16 and b.m == 3, f"band {b.closed} {len(b.columns)} {b.m}")
    expect(b.mode == core.FIT_CIRCLE, "a round bevel should fit the exact profile")
    expect(abs(b.profile - 0.5) < 1e-6, f"profile {b.profile}")
    expect(run_op(delta=3) == {'FINISHED'}, "op failed")
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 16 * 8, f"verts {len(bm.verts)}")
    expect(on_bevel_arc(bm) < 1e-6, f"off the arc by {on_bevel_arc(bm)}")
    got = column_angles(bm)
    expect(close_to(got, range(0, 91, 15)), f"angles {got}")
    expect(not check_topology(bm), check_topology(bm))
    expect(sum(e.select for e in bm.edges) == 6, f"{sum(e.select for e in bm.edges)} edges selected")
    # The selection is the new column, so the tool runs again right away.
    run_op(delta=-4)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 16 * 4, f"verts {len(bm.verts)}")
    expect(close_to(column_angles(bm), (0, 45, 90)), f"{column_angles(bm)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_keeps_spacing_unless_even():
    ob = make_profile_cylinder([(1.0, -1.0)] + arc(angles=(0, 10, 30, 90)))
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    before = [v.co.copy() for v in bmesh.from_edit_mesh(ob.data).verts]
    run_op(delta=0)
    after = [v.co.copy() for v in bmesh.from_edit_mesh(ob.data).verts]
    expect(max((a - b).length for a, b in zip(before, after)) < 1e-9, "delta 0 must not move anything")
    run_op(delta=3)
    got = column_angles(bmesh.from_edit_mesh(ob.data))
    expect(close_to(got, (0, 5, 10, 20, 30, 60, 90)), f"kept spacing {got}")


@test
def curve_even_spacing_in_place():
    ob = make_profile_cylinder([(1.0, -1.0)] + arc(angles=(0, 10, 30, 90)))
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    nv = len(bm.verts)
    run_op(delta=0, even_spacing=True)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == nv, "count changed")
    got = column_angles(bm)
    expect(close_to(got, (0, 30, 60, 90)), f"even {got}")
    expect(on_bevel_arc(bm) < 1e-6, "left the arc")


@test
def curve_chamfer_becomes_round():
    prof = [(1.0, -1.0), (1.0, 0.7), (0.7, 1.0)]
    ob = make_profile_cylinder(prof)
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1 and bands[0].m == 1, "chamfer band")
    expect(abs(bands[0].profile - 0.25) < 1e-6, f"a chamfer is profile 0.25, got {bands[0].profile}")
    run_op(delta=3)  # still straight: 4 segments along the chamfer
    bm = bmesh.from_edit_mesh(ob.data)
    expect(flatness(bm) < 1e-6, f"not on the chamfer {flatness(bm)}")
    expect(len(bm.verts) == 16 * 6, f"verts {len(bm.verts)}")
    expect(not check_topology(bm), check_topology(bm))

    reset()
    ob = make_profile_cylinder(prof)
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=3, profile_shift=0.25)  # 0.25 -> 0.5: round
    bm = bmesh.from_edit_mesh(ob.data)
    expect(on_bevel_arc(bm) < 1e-6, f"not round: {on_bevel_arc(bm)}")
    got = column_angles(bm)
    expect(close_to(got, (0, 22.5, 45, 67.5, 90)), f"{got}")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_tightness():
    def to_corner(bm):
        ds = [math.hypot(v.co.x - 1.0, v.co.z - 1.0) for v in bm.verts
              if abs(v.co.y) < 1e-6 and v.co.x > 0.7 + 1e-6 and v.co.z > 0.7 + 1e-6]
        return sum(ds) / len(ds)

    ob = make_profile_cylinder([(1.0, -1.0)] + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    d0 = to_corner(bmesh.from_edit_mesh(ob.data))
    run_op(profile_shift=0.3)
    bm = bmesh.from_edit_mesh(ob.data)
    d1 = to_corner(bm)
    expect(d1 < d0 * 0.8, f"a tighter profile should move toward the corner ({d0:.4f} -> {d1:.4f})")
    expect(not check_topology(bm), check_topology(bm))
    bands = curves.analyze(bm)
    expect(abs(bands[0].profile - 0.8) < 0.02, f"profile after {bands[0].profile}")
    run_op(profile_shift=0.25 - bands[0].profile)  # flatten to a chamfer
    bm = bmesh.from_edit_mesh(ob.data)
    expect(flatness(bm) < 1e-4, f"profile 0.25 should be straight ({flatness(bm)})")


@test
def curve_arc_on_plain_cylinder():
    bpy.ops.mesh.primitive_cylinder_add(vertices=16)
    ob = bpy.context.object
    bm = edit(ob)
    top = sorted((v for v in bm.verts if abs(v.co.z - 1) < 1e-5),
                 key=lambda v: math.atan2(v.co.y, v.co.x) % TAU)
    arc_verts = set(top[:5])
    select_edges(bm, lambda e: all(v in arc_verts for v in e.verts))
    bmesh.update_edit_mesh(ob.data)
    expect(not core.analyze(bmesh.from_edit_mesh(ob.data)), "an open arc is not a ring")
    run_op(delta=4)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(ring_count_z(bm, 1.0) == 20 and ring_count_z(bm, -1.0) == 20,
           f"rings {ring_count_z(bm, 1.0)} {ring_count_z(bm, -1.0)}")
    expect(sorted(len(f.verts) for f in bm.faces if len(f.verts) > 4) == [20, 20], "caps")
    expect(not check_topology(bm), check_topology(bm))
    worst = max(abs(v.co.xy.length - 1.0) for v in bm.verts)
    expect(worst < 1e-6, f"new verts off the circle by {worst}")


@test
def curve_two_bevels():
    ob = make_profile_cylinder(arc(top=False) + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    select_column(bm, -1.0, -0.7)
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 2, f"bands {len(bands)}")
    run_op(delta=2)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 16 * 12, f"verts {len(bm.verts)}")
    expect(on_bevel_arc(bm) < 1e-6, "top arc")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_blender_bevel():
    ob = make_beveled_cylinder(16, 3)
    bm = edit(ob)
    t = max(bm.verts, key=lambda v: (round(v.co.z, 4), v.co.x))
    d = t.co.xy.normalized()
    select_column(bm, 0.8 - 1e-4, direction=(d.x, d.y))
    bmesh.update_edit_mesh(ob.data)
    nv = len(bm.verts)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1 and bands[0].m == 3 and bands[0].closed,
           f"bevel band {[(b.m, b.closed, len(b.columns)) for b in bands]}")
    expect(bands[0].profile is not None and abs(bands[0].profile - 0.5) < 0.05, f"profile {bands[0].profile}")
    run_op(delta=2)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == nv + 32, f"verts {len(bm.verts)} (was {nv})")
    expect(not check_topology(bm), check_topology(bm))


@test
def radius_scale_selected_ring():
    ob = make_stacked_cylinder()
    bm = bmesh.from_edit_mesh(ob.data)
    select_edges(bm, ring_pred(0.0))
    bmesh.update_edit_mesh(ob.data)
    run_selected(radius_scale=1.5)
    bm = bmesh.from_edit_mesh(ob.data)
    rs = {round(v.co.z, 3): round(v.co.xy.length, 5) for v in bm.verts if v.co.xy.length > 1e-6}
    expect(rs == {-1.0: 1.0, -0.5: 1.0, 0.0: 1.5, 0.5: 1.0, 1.0: 1.0}, f"radii {rs}")
    expect(ring_counts(bm) == [16] * 5, "counts")
    run_op(radius_scale=0.5, delta=4)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(ring_counts(bm) == [20] * 5, f"{ring_counts(bm)}")
    rs = {round(v.co.z, 3): round(v.co.xy.length, 5) for v in bm.verts if v.co.xy.length > 1e-6}
    expect(rs == {-1.0: 0.5, -0.5: 0.5, 0.0: 0.75, 0.5: 0.5, 1.0: 0.5}, f"radii {rs}")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_open_band_cube_bevel():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    edge = next(e for e in bm.edges if all(v.co.x > 0.9 and v.co.z > 0.9 for v in e.verts))
    bmesh.ops.bevel(bm, geom=[edge], offset=0.3, segments=3, profile=0.5, affect='EDGES')
    ob = new_object_from_bm(bm, "Cube")
    bm = edit(ob, 'VERT')
    for v in bm.verts:
        v.select = v.co.y > 0.999 and v.co.x > 0.69 and v.co.z > 0.69
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1 and not bands[0].closed and len(bands[0].columns) == 2,
           f"{[(b.m, b.closed, len(b.columns)) for b in bands]}")
    expect(abs(bands[0].profile - 0.5) < 1e-3, f"profile {bands[0].profile}")
    nv = len(bmesh.from_edit_mesh(ob.data).verts)
    run_op(delta=3)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == nv + 6, f"verts {len(bm.verts)} (was {nv})")
    expect(not check_topology(bm), check_topology(bm))
    worst = max(abs(math.hypot(v.co.x - 0.7, v.co.z - 0.7) - 0.3) for v in bm.verts
                if v.co.x > 0.7 + 1e-6 and v.co.z > 0.7 + 1e-6)
    expect(worst < 1e-5, f"off the bevel arc by {worst}")
    expect(sum(v.select for v in bm.verts) == 7, f"{sum(v.select for v in bm.verts)} verts selected")


def select_top_arc(bm, segments):
    top = sorted((v for v in bm.verts if abs(v.co.z - 1) < 1e-5),
                 key=lambda v: math.atan2(v.co.y, v.co.x) % TAU)
    chosen = set(top[:segments + 1])
    select_edges(bm, lambda e: all(v in chosen for v in e.verts))
    return [v.co.copy() for v in top[:segments + 1]]


@test
def curve_half_ring_arc():
    # Half of a cylinder's top loop (8 of 16 edges): no corner at the ends.
    bpy.ops.mesh.primitive_cylinder_add(vertices=16)
    ob = bpy.context.object
    bm = edit(ob)
    ends = select_top_arc(bm, 8)
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1 and bands[0].m == 8, f"{[(b.m, len(b.columns)) for b in bands]}")
    expect(bands[0].mode == core.FIT_CIRCLE, "a half circle should fit an exact arc")
    expect(bands[0].profile is not None and abs(bands[0].profile - 0.5) < 1e-3,
           f"a round arc should have tension 0.5, got {bands[0].profile}")
    run_op(delta=4)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(ring_count_z(bm, 1.0) == 20 and ring_count_z(bm, -1.0) == 20, "counts")
    worst = max(abs(v.co.xy.length - 1.0) for v in bm.verts)
    expect(worst < 1e-6, f"new verts off the circle by {worst}")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_half_ring_tension():
    bpy.ops.mesh.primitive_cylinder_add(vertices=16)
    ob = bpy.context.object
    bm = edit(ob)
    arc_pts = select_top_arc(bm, 8)
    bmesh.update_edit_mesh(ob.data)
    run_op(profile_shift=0.3)
    bm = bmesh.from_edit_mesh(ob.data)
    top = [v.co for v in bm.verts if abs(v.co.z - 1) < 1e-5]
    rmax = max(p.xy.length for p in top)
    expect(rmax > 1.1, f"tighter tension should push the arc toward a box ({rmax:.3f})")
    for p in (arc_pts[0], arc_pts[4], arc_pts[8]):  # ends and the split point stay
        expect(min((q - p).length for q in top) < 1e-6, f"{p} moved")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_half_ring_radius():
    bpy.ops.mesh.primitive_cylinder_add(vertices=16)
    ob = bpy.context.object
    bm = edit(ob)
    arc_pts = select_top_arc(bm, 8)
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=1.5)
    bm = bmesh.from_edit_mesh(ob.data)
    on_arc = [v for v in bm.verts if abs(v.co.z - 1) < 1e-5 and abs(v.co.xy.length - 1.5) < 1e-6]
    expect(len(on_arc) == 9, f"{len(on_arc)} top verts at radius 1.5 (expected the 9 arc verts)")
    rest = [v for v in bm.verts if abs(v.co.z - 1) < 1e-5 and abs(v.co.xy.length - 1.0) < 1e-6]
    expect(len(rest) == 7, f"{len(rest)} untouched top verts")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_bevel_radius():
    ob = make_profile_cylinder([(1.0, -1.0)] + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=2.0, delta=2)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 16 * 7, f"verts {len(bm.verts)}")
    expect(on_bevel_arc(bm, 0.6) < 1e-6, f"bevel radius should double: off by {on_bevel_arc(bm, 0.6)}")
    expect(ring_count_z(bm, 0.4) == 16, "side ring should slide down to z=0.4")
    caps = [f for f in bm.faces if len(f.verts) == 16 and all(abs(v.co.z - 1) < 1e-6 for v in f.verts)]
    expect(caps and abs(caps[0].verts[0].co.xy.length - 0.4) < 1e-6, "cap ring should shrink to r=0.4")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_radius_with_wrapped_bevel():
    # A big bevel whose front end is wrapped by a small bevel around the front
    # face: the wrap loops are columns of the same band and must stay parallel.
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    e = next(e for e in bm.edges if all(v.co.x > 0.9 and v.co.z > 0.9 for v in e.verts))
    bmesh.ops.bevel(bm, geom=[e], offset=0.3, segments=4, profile=0.5, affect='EDGES')
    front = [e for e in bm.edges if all(v.co.y < -0.999 for v in e.verts)]
    bmesh.ops.bevel(bm, geom=front, offset=0.08, segments=3, profile=0.5, affect='EDGES')
    ob = new_object_from_bm(bm, "Wrapped")
    bm = edit(ob)
    select_edges(bm, lambda e: all(v.co.y > 0.999 and v.co.x > 0.69 and v.co.z > 0.69 for v in e.verts))
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1 and len(bands[0].columns) == 5, f"{[len(b.columns) for b in bands]}")
    ends_before = [(c[0].co.x, c[-1].co.z) for c in bands[0].columns]
    run_op(radius_scale=2.5)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(not check_topology(bm), check_topology(bm))
    bands = curves.analyze(bm)
    ends = [(c[0].co.x, c[-1].co.z) for c in bands[0].columns]
    # main bevel: width 0.3 -> 0.75; every column moved by the same 0.45
    for (xb, zb), (xa, za) in zip(ends_before, ends):
        expect(abs((xb - xa) - 0.45) < 1e-3 and abs((zb - za) - 0.45) < 1e-3,
               f"column ends moved by {xb - xa:.4f}, {zb - za:.4f} (expected 0.45)")


@test
def curve_half_ring_flattens_as_one_curve():
    # Lower tension on a half ring flattens the whole arc toward its chord,
    # not each quarter toward its own chord (which made a notch).
    def setup():
        reset()
        bpy.ops.mesh.primitive_cylinder_add(vertices=12)
        ob = bpy.context.object
        bm = edit(ob)
        pts = select_top_arc(bm, 6)
        bmesh.update_edit_mesh(ob.data)
        return ob, pts

    ob, pts = setup()
    a, b = pts[0], pts[-1]
    mid = pts[3]
    base = curves.analyze(bmesh.from_edit_mesh(ob.data))[0].profile
    expect(abs(base - 0.5) < 1e-6, f"half ring tension {base}")
    run_op(profile_shift=0.25 - base)  # straight, as Ctrl-snapping to 0.25 does
    bm = bmesh.from_edit_mesh(ob.data)
    d = (b - a).normalized()
    arc_side = (mid - (a + b) / 2).normalized()
    on_side = [v.co for v in bm.verts if abs(v.co.z - 1) < 1e-5 and (v.co - (a + b) / 2).dot(arc_side) > -1e-6]
    worst = max(((p - a) - d * (p - a).dot(d)).length for p in on_side)
    expect(len(on_side) == 7 and worst < 1e-6, f"{len(on_side)} verts, {worst:.2e} off the chord")
    expect(not check_topology(bm), check_topology(bm))

    ob, pts = setup()
    run_op(profile_shift=-0.125)  # half the bulge, still one smooth arc
    bm = bmesh.from_edit_mesh(ob.data)
    top = sorted((v.co.copy() for v in bm.verts if abs(v.co.z - 1) < 1e-5 and (v.co - (a + b) / 2).dot(arc_side) > 1e-6),
                 key=lambda p: (p - a).dot(d))
    h = [(p - (a + b) / 2).dot(arc_side) for p in top]
    expect(abs(max(h) - 0.5) < 1e-6, f"apex height {max(h):.4f} (expected 0.5)")
    peak = h.index(max(h))
    expect(all(x < y for x, y in zip(h[:peak], h[1:peak + 1])) and all(x > y for x, y in zip(h[peak:], h[peak + 1:])),
           f"heights should rise then fall, no notch: {[round(x, 3) for x in h]}")


@test
def curve_radius_zero_removes_cylinder_bevel():
    ob = make_profile_cylinder([(1.0, -1.0)] + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    expect(run_op(radius_scale=0.0) == {'FINISHED'}, "op failed")
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 32 and len(bm.faces) == 18, f"{len(bm.verts)} verts, {len(bm.faces)} faces")
    expect(ring_count_z(bm, 1.0) == 16 and all(abs(v.co.xy.length - 1.0) < 1e-6 for v in bm.verts),
           "the bevel should collapse onto the sharp edge at r=1, z=1")
    expect(not check_topology(bm), check_topology(bm))
    expect(sum(e.select for e in bm.edges) == 16, f"{sum(e.select for e in bm.edges)} edges selected (the new sharp edge)")


@test
def curve_radius_zero_removes_cube_bevel():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    e = next(e for e in bm.edges if all(v.co.x > 0.9 and v.co.z > 0.9 for v in e.verts))
    bmesh.ops.bevel(bm, geom=[e], offset=0.3, segments=4, profile=0.5, affect='EDGES')
    ob = new_object_from_bm(bm, "Cube")
    bm = edit(ob)
    select_edges(bm, lambda e: all(v.co.y > 0.999 and v.co.x > 0.69 and v.co.z > 0.69 for v in e.verts))
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=0.0)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 8 and len(bm.faces) == 6, f"{len(bm.verts)} verts, {len(bm.faces)} faces: should be a plain cube")
    expect(all(abs(abs(c) - 1.0) < 1e-6 for v in bm.verts for c in v.co), "corners")
    expect(not check_topology(bm), check_topology(bm))


@test
def ring_radius_zero_does_not_collapse():
    ob = make_stacked_cylinder()
    bm = bmesh.from_edit_mesh(ob.data)
    select_edges(bm, ring_pred(0.0))
    bmesh.update_edit_mesh(ob.data)
    run_selected(radius_scale=0.0)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(ring_counts(bm) == [16] * 5, "loops keep their vertices")


from roundabout import restore  # noqa: E402


@test
def curve_collapse_then_restore():
    ob = make_profile_cylinder([(1.0, -1.0)] + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=0.0)
    expect(len(bmesh.from_edit_mesh(ob.data).verts) == 32, "collapsed")
    # The collapsed edge is still selected: the tool now restores the bevel.
    run_op(radius_scale=1.0)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 80, f"restored verts {len(bm.verts)} (expected 80)")
    expect(on_bevel_arc(bm) < 0.01, f"restored bevel off the arc by {on_bevel_arc(bm):.4f}")
    expect(not check_topology(bm), check_topology(bm))
    sel = [e for e in bm.edges if e.select]
    expect(len(sel) == 3, f"{len(sel)} edges selected (expected the 3-segment profile)")
    # ...and the restored profile is an ordinary curve again.
    bands = curves.analyze(bm)
    expect(len(bands) == 1 and bands[0].m == 3 and bands[0].closed, "restored profile should be a curve band")


@test
def curve_restore_half_width_and_segments():
    ob = make_profile_cylinder([(1.0, -1.0)] + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=0.0)
    run_op(radius_scale=0.5, delta=2)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 16 * 7, f"verts {len(bm.verts)}")
    expect(ring_count_z(bm, 0.85) == 16, "half width: the side ring at z = 0.85")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_restore_cube_bevel():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    e = next(e for e in bm.edges if all(v.co.x > 0.9 and v.co.z > 0.9 for v in e.verts))
    bmesh.ops.bevel(bm, geom=[e], offset=0.3, segments=4, profile=0.5, affect='EDGES')
    ob = new_object_from_bm(bm, "Cube")
    bm = edit(ob)
    nv = len(bm.verts)
    select_edges(bm, lambda e: all(v.co.y > 0.999 and v.co.x > 0.69 and v.co.z > 0.69 for v in e.verts))
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=0.0)
    expect(len(bmesh.from_edit_mesh(ob.data).verts) == 8, "collapsed to a cube")
    run_op(radius_scale=1.0)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == nv, f"restored {len(bm.verts)} verts, expected {nv}")
    worst = max(abs(math.hypot(v.co.x - 0.7, v.co.z - 0.7) - 0.3) for v in bm.verts
                if v.co.x > 0.7 + 1e-6 and v.co.z > 0.7 + 1e-6)
    expect(worst < 1e-4, f"restored bevel off the arc by {worst}")
    expect(not check_topology(bm), check_topology(bm))


@test
def curve_restore_forgotten_after_edit_mode():
    ob = make_profile_cylinder([(1.0, -1.0)] + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=0.0)
    bpy.ops.object.mode_set(mode='OBJECT')
    restore.on_depsgraph_update()  # what the depsgraph handler does on the mode switch
    bm = edit(ob)
    select_edges(bm, ring_pred(1.0))
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=1.0)
    expect(len(bmesh.from_edit_mesh(ob.data).verts) == 32, "after leaving edit mode the edge is just a loop")


@test
def remembered_collapse_ignored_when_more_is_selected():
    # A remembered collapse only comes back when nothing else is selected:
    # selecting it together with other loops scales those loops instead.
    ob = make_profile_cylinder([(1.0, -1.0)] + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=0.0)
    bm = bmesh.from_edit_mesh(ob.data)
    top, bottom = ring_pred(1.0), ring_pred(-1.0)
    select_edges(bm, lambda e: top(e) or bottom(e))
    bmesh.update_edit_mesh(ob.data)
    expect(run_op(radius_scale=1.5) == {'FINISHED'}, "op failed")
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 32, f"{len(bm.verts)} verts: the bevel came back instead of scaling the loops")
    r = max(v.co.xy.length for v in bm.verts)
    expect(abs(r - 1.5) < 1e-3, f"loops should be scaled to radius 1.5, got {r:.4f}")
    expect(not check_topology(bm), check_topology(bm))


@test
def plain_edge_radius_does_nothing():
    # A plain corner edge of a box is not a bevel: radius and collapse leave it alone.
    for scale in (0.0, 0.5, 2.0):
        reset()
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        ob = bpy.context.object
        bm = edit(ob)
        select_edges(bm, lambda e: all(v.co.x > 0.9 and v.co.z > 0.9 for v in e.verts))
        bmesh.update_edit_mesh(ob.data)
        bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
        expect(bands and not any(b.has_radius() for b in bands), "a plain edge should have no radius")
        before = sorted(tuple(round(c, 6) for c in v.co) for v in bmesh.from_edit_mesh(ob.data).verts)
        run_op(radius_scale=scale)
        bm = bmesh.from_edit_mesh(ob.data)
        after = sorted(tuple(round(c, 6) for c in v.co) for v in bm.verts)
        expect(after == before and len(bm.faces) == 6, f"radius {scale} changed a plain cube")


@test
def box_top_chamfer_still_collapses():
    # All four top edges chamfered (mitered corners, plain quad top): still a
    # chamfer with a radius, so collapsing turns it back into a cube.
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.bevel(bm, geom=[e for e in bm.edges if all(v.co.z > 0.9 for v in e.verts)],
                    offset=0.3, segments=1, profile=0.5, affect='EDGES')
    ob = new_object_from_bm(bm, "Box")
    bm = edit(ob)
    side = [v for v in bm.verts if abs(v.co.x - 1) < 1e-5 and abs(v.co.z - 0.7) < 1e-5]
    a = max(side, key=lambda v: v.co.y)
    b = max((e.other_vert(a) for e in a.link_edges), key=lambda v: v.co.z)
    select_edges(bm, lambda e: set(e.verts) == {a, b})
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1 and bands[0].has_radius(), "box chamfer should have a radius")
    run_op(radius_scale=0.0)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 8 and len(bm.faces) == 6, f"{len(bm.verts)} verts, {len(bm.faces)} faces (expected a cube)")
    expect(not check_topology(bm), check_topology(bm))


@test
def chamfer_radius_with_triangulated_cap():
    # The cap's triangulation diagonals must not skew the chamfer's corner:
    # every column scales the same.
    def build():
        reset()
        ob = make_profile_cylinder([(1.0, -1.0), (1.0, 0.7), (0.7, 1.0)], n=12)
        bm = edit(ob)
        cap = next(f for f in bm.faces if len(f.verts) == 12 and all(abs(v.co.z - 1) < 1e-6 for v in f.verts))
        bmesh.ops.triangulate(bm, faces=[cap], quad_method='BEAUTY', ngon_method='BEAUTY')
        select_column(bm, 0.7)
        bmesh.update_edit_mesh(ob.data)
        return ob

    ob = build()
    run_op(radius_scale=1.5)
    bm = bmesh.from_edit_mesh(ob.data)
    cap_r = {round(v.co.xy.length, 5) for v in bm.verts if abs(v.co.z - 1) < 1e-6 and v.co.xy.length > 1e-6}
    side_z = {round(v.co.z, 5) for v in bm.verts if abs(v.co.xy.length - 1) < 1e-6 and v.co.z > -0.9}
    expect(cap_r == {0.55} and side_z == {0.55}, f"uneven chamfer: cap radii {cap_r}, side heights {side_z}")
    expect(not check_topology(bm), check_topology(bm))

    ob = build()
    run_op(radius_scale=0.0)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(all(abs(v.co.xy.length - 1.0) < 1e-6 for v in bm.verts if v.co.xy.length > 0.9 or abs(v.co.z - 1) > 1e-6),
           "collapsing should give a sharp edge at r=1, z=1")
    expect(not check_topology(bm), check_topology(bm))


@test
def bevel_radius_with_triangulated_cap():
    ob = make_profile_cylinder([(1.0, -1.0)] + arc(), n=12)
    bm = edit(ob)
    cap = next(f for f in bm.faces if len(f.verts) == 12 and all(abs(v.co.z - 1) < 1e-6 for v in f.verts))
    bmesh.ops.triangulate(bm, faces=[cap], quad_method='BEAUTY', ngon_method='BEAUTY')
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    run_op(radius_scale=1.5)
    bm = bmesh.from_edit_mesh(ob.data)
    cap_r = {round(v.co.xy.length, 5) for v in bm.verts if abs(v.co.z - 1) < 1e-6 and v.co.xy.length > 1e-6}
    side_z = {round(v.co.z, 5) for v in bm.verts if abs(v.co.xy.length - 1) < 1e-6 and v.co.z > -0.9}
    expect(cap_r == {0.55} and side_z == {0.55}, f"uneven bevel: cap radii {cap_r}, side heights {side_z}")
    expect(on_bevel_arc(bm, 0.45) < 1e-5, f"bevel off its arc by {on_bevel_arc(bm, 0.45)}")
    expect(not check_topology(bm), check_topology(bm))


@test
def chamfer_loop_radius_with_sharp_corners():
    # A chamfer all around a D-shaped plate (half ellipse + one long straight
    # edge, fan-triangulated cap). At the two sharp corners the neighbouring
    # rim edges differ a lot in length; the miter columns must still find
    # their corner, or radius is off for the whole loop.
    pts = [(1.5 * math.cos(math.pi * i / 23), math.sin(math.pi * i / 23)) for i in range(24)] + [(0.0, -0.2)]
    bm = bmesh.new()
    bot = [bm.verts.new((x, y, 0.0)) for x, y in pts]
    top = [bm.verts.new((x, y, 0.5)) for x, y in pts]
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((bot[i], bot[j], top[j], top[i]))
    bm.faces.new(top)
    bm.faces.new(list(reversed(bot)))
    bm.normal_update()
    rim = [e for e in bm.edges if all(abs(v.co.z - 0.5) < 1e-6 for v in e.verts)]
    bmesh.ops.bevel(bm, geom=rim, offset=0.05, segments=1, affect='EDGES')
    cap = max(bm.faces, key=lambda f: len(f.verts) if all(abs(v.co.z - 0.5) < 1e-6 for v in f.verts) else 0)
    bmesh.ops.triangulate(bm, faces=[cap], ngon_method='BEAUTY')
    ob = new_object_from_bm(bm, "Plate")
    bm = edit(ob)
    select_edges(bm, lambda e: min(v.co.z for v in e.verts) > 0.4 and abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-6)
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1 and bands[0].closed and bands[0].has_radius(),
           f"{[(b.m, len(b.columns), b.closed, b.has_radius()) for b in bands]}")
    run_op(radius_scale=1.5)
    bm = bmesh.from_edit_mesh(ob.data)
    wall = {round(v.co.z, 5) for v in bm.verts if 0.3 < v.co.z < 0.49}
    expect(wall == {0.425}, f"chamfer wall edge heights {wall} (expected all at 0.425)")
    expect(not check_topology(bm), check_topology(bm))


@test
def chamfer_loop_radius_with_twisted_columns():
    # A hand-made chamfer: its inner edge is nudged along the rim, so the two
    # legs of every column pass each other instead of meeting. Radius must
    # still work on the whole loop.
    n = 24
    bm = bmesh.new()
    bot = [bm.verts.new((math.cos(TAU * i / n), math.sin(TAU * i / n), 0.0)) for i in range(n)]
    top = [bm.verts.new((math.cos(TAU * i / n), math.sin(TAU * i / n), 0.5)) for i in range(n)]
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((bot[i], bot[j], top[j], top[i]))
    bm.faces.new(top)
    bm.faces.new(list(reversed(bot)))
    bm.normal_update()
    rim = [e for e in bm.edges if all(abs(v.co.z - 0.5) < 1e-6 for v in e.verts)]
    bmesh.ops.bevel(bm, geom=rim, offset=0.05, segments=1, affect='EDGES')
    for k, v in enumerate(v for v in bm.verts if abs(v.co.z - 0.5) < 1e-6):
        a = math.atan2(v.co.y, v.co.x) + (0.008 if k % 2 else -0.008)  # ~0.008 along the rim
        r = v.co.xy.length
        v.co.x, v.co.y = r * math.cos(a), r * math.sin(a)
    ob = new_object_from_bm(bm, "Twisted")
    bm = edit(ob)
    select_edges(bm, lambda e: min(v.co.z for v in e.verts) > 0.4 and abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-6)
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1 and bands[0].has_radius(),
           f"{[(b.m, len(b.columns), b.has_radius()) for b in bands]}")
    run_op(radius_scale=1.5)
    bm = bmesh.from_edit_mesh(ob.data)
    wall = [v.co.z for v in bm.verts if 0.3 < v.co.z < 0.49]
    expect(len(wall) == n and max(abs(z - 0.425) for z in wall) < 2e-3,
           f"chamfer wall edge heights {sorted({round(z, 4) for z in wall})} (expected about 0.425)")
    expect(not check_topology(bm), check_topology(bm))


def make_tube_rim(n_out=16, n_in=16, twist=0.5):
    """Tube top: outer and inner rim loops joined by a zig-zag of triangles
    (the inner loop turned by ``twist`` of a step), each with a wall down."""
    bm = bmesh.new()
    def ring(n, r, z, off):
        return [bm.verts.new((r * math.cos(TAU * (k + off) / n), r * math.sin(TAU * (k + off) / n), z)) for k in range(n)]
    O, I = ring(n_out, 1.0, 1.0, 0.0), ring(n_in, 0.8, 1.0, twist)
    Ob, Ib = ring(n_out, 1.0, 0.0, 0.0), ring(n_in, 0.8, 0.0, twist)
    for R, Rb in ((O, Ob), (I, Ib)):
        n = len(R)
        for k in range(n):
            bm.faces.new((R[k], R[(k + 1) % n], Rb[(k + 1) % n], Rb[k]))
    # zig-zag between O and I by angle
    ang = lambda v: math.atan2(v.co.y, v.co.x) % TAU  # noqa: E731
    i = j = 0
    io, ii = sorted(range(n_out), key=lambda k: ang(O[k])), sorted(range(n_in), key=lambda k: ang(I[k]))
    while i < n_out or j < n_in:
        a0, a1 = O[io[i % n_out]], O[io[(i + 1) % n_out]]
        b0, b1 = I[ii[j % n_in]], I[ii[(j + 1) % n_in]]
        ta = ang(a1) + (TAU if i + 1 >= n_out else 0.0)
        tb = ang(b1) + (TAU if j + 1 >= n_in else 0.0) + (TAU if ang(b1) < ang(I[ii[0]]) else 0.0)
        if j >= n_in or (i < n_out and ta <= tb):
            bm.faces.new((a0, a1, b0))
            i += 1
        else:
            bm.faces.new((a0, b1, b0))
            j += 1
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    ob = new_object_from_bm(bm, "Tube")
    bm = edit(ob)
    select_edges(bm, lambda e: all(abs(v.co.z - 1.0) < 1e-6 for v in e.verts)
                 and abs(e.verts[0].co.xy.length - e.verts[1].co.xy.length) < 1e-6)
    bmesh.update_edit_mesh(ob.data)
    return ob


def rim_faces(bm):
    return [f for f in bm.faces if all(abs(v.co.z - 1.0) < 1e-6 for v in f.verts)]


@test
def selected_loops_zigzag_rim_becomes_quads():
    # Two selected loops joined by a zig-zag strip (they don't line up). With
    # the same new count, the strip is rebuilt as clean, radial quads; with
    # different counts it stays a clean zig-zag, and matching again cleans it.
    ob = make_tube_rim()
    for segs in (6, 4, 16, 23):
        expect(run_op(use_absolute=True, segments=segs, whole_shape=False) == {'FINISHED'}, "op failed")
        bm = bmesh.from_edit_mesh(ob.data)
        rim = rim_faces(bm)
        expect(len(rim) == segs and all(len(f.verts) == 4 for f in rim),
               f"{segs}: rim faces {sorted(len(f.verts) for f in rim)} (expected {segs} quads)")
        worst = 0.0
        for f in rim:
            for e in f.edges:
                a, b = e.verts
                if abs(a.co.xy.length - b.co.xy.length) > 0.1:
                    worst = max(worst, abs(a.co.xy.angle_signed(b.co.xy)))
        expect(worst < 1e-4, f"{segs}: rim edges not radial (off by {math.degrees(worst):.2f} deg)")
        expect(not check_topology(bm, expect_boundary=None), check_topology(bm, expect_boundary=None))

    reset()
    ob = make_tube_rim(16, 12, twist=0.3)
    run_op(delta=4, whole_shape=False)
    bm = bmesh.from_edit_mesh(ob.data)
    rim = rim_faces(bm)
    expect(sorted({len(f.verts) for f in rim}) in ([3], [3, 4]), f"different counts: rim face sizes {sorted(len(f.verts) for f in rim)}")
    expect(not check_topology(bm, expect_boundary=None), check_topology(bm, expect_boundary=None))
    run_op(use_absolute=True, segments=20, whole_shape=False)
    bm = bmesh.from_edit_mesh(ob.data)
    rim = rim_faces(bm)
    expect(len(rim) == 20 and all(len(f.verts) == 4 for f in rim), f"matched again: rim {sorted(len(f.verts) for f in rim)}")


@test
def tidy_pattern_is_even_and_symmetric():
    for na, nb in ((11, 8), (8, 11), (13, 17), (20, 16), (7, 2), (9, 9), (30, 5)):
        for closed in (False, True):
            k = core.tidy_pattern(na, nb, closed)
            expect(k.count('Q') + k.count('A') == na and k.count('Q') + k.count('B') == nb,
                   f"{na}/{nb} closed={closed}: {''.join(k)} does not use every segment once")
            T, d = max(na, nb), abs(na - nb)
            rare = min(d, T - d)
            if not closed and not (T % 2 == 0 and rare % 2 == 1):  # (no middle step to put the odd one on)
                expect(k == k[::-1], f"{na}/{nb}: open pattern {''.join(k)} is not mirrored")
            tris = [i for i, x in enumerate(k) if x != 'Q']
            if len(tris) > 1:
                gaps = [b - a for a, b in zip(tris, tris[1:])]
                if closed:
                    gaps.append(tris[0] + len(k) - tris[-1])
                expect(max(gaps) - min(gaps) <= 1 or not closed and len(set(gaps)) <= 2,
                       f"{na}/{nb} closed={closed}: uneven pattern {''.join(k)}")


@test
def tidy_untwists_quad_strip_between_selected_loops():
    # Outer and inner rim loops joined by clean quads that are twisted (each
    # rung leans 40 % of a step). Without tidy the twist is kept; with tidy
    # the loops turn to line up and every rung takes the shortest way.
    def build():
        reset()
        bm = bmesh.new()
        n = 16
        def ring(r, z, off):
            return [bm.verts.new((r * math.cos(TAU * (k + off) / n), r * math.sin(TAU * (k + off) / n), z))
                    for k in range(n)]
        O, I = ring(1.0, 1.0, 0.0), ring(0.8, 1.0, 0.4)
        Ob, Ib = ring(1.0, 0.0, 0.0), ring(0.8, 0.0, 0.4)
        for A, B in ((O, Ob), (I, Ib), (O, I)):
            for k in range(n):
                bm.faces.new((A[k], A[(k + 1) % n], B[(k + 1) % n], B[k]))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        ob = new_object_from_bm(bm, "Twisted")
        bm = edit(ob)
        select_edges(bm, lambda e: all(abs(v.co.z - 1.0) < 1e-6 for v in e.verts)
                     and abs(e.verts[0].co.xy.length - e.verts[1].co.xy.length) < 1e-6)
        bmesh.update_edit_mesh(ob.data)
        return ob

    def twist(bm):
        worst = 0.0
        for f in rim_faces(bm):
            for e in f.edges:
                a, b = e.verts
                if abs(a.co.xy.length - b.co.xy.length) > 0.1:
                    worst = max(worst, abs(a.co.xy.angle_signed(b.co.xy)))
        return math.degrees(worst)

    ob = build()
    run_op(delta=2, whole_shape=False)
    expect(twist(bmesh.from_edit_mesh(ob.data)) > 5.0, "without tidy the twisted strip should stay twisted")
    for kw in (dict(delta=0), dict(delta=2), dict(delta=-6)):
        ob = build()
        run_op(whole_shape=False, tidy=True, **kw)
        bm = bmesh.from_edit_mesh(ob.data)
        rim = rim_faces(bm)
        expect(all(len(f.verts) == 4 for f in rim) and twist(bm) < 1e-3,
               f"{kw}: rim {sorted(len(f.verts) for f in rim)}, rungs off radial by {twist(bm):.2f} deg")
        expect(not check_topology(bm, expect_boundary=None), check_topology(bm, expect_boundary=None))


@test
def curve_tidy_spreads_triangles_symmetrically():
    # One arc of a stacked cylinder, 8 -> 11 segments in selected mode with
    # tidy connections: each side strip gets 3 triangles, evenly spread and
    # mirrored about the middle (not clumps of triangles).
    ob = make_stacked_cylinder(n=16, uvs=False)
    bm = edit(ob)
    top = sorted((v for v in bm.verts if abs(v.co.z) < 1e-5), key=lambda v: math.atan2(v.co.y, v.co.x) % TAU)[:9]
    chosen = set(top)
    select_edges(bm, lambda e: all(v in chosen for v in e.verts))
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=3, whole_shape=False, tidy=True)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(not check_topology(bm, expect_boundary=None), check_topology(bm, expect_boundary=None))
    for z0, z1 in ((0.0, 0.5), (-0.5, 0.0)):
        fs = [f for f in bm.faces if all(z0 - 1e-5 <= v.co.z <= z1 + 1e-5 for v in f.verts)
              and len({round(v.co.z, 4) for v in f.verts}) == 2]
        fs = [f for f in fs if 0.0 < math.atan2(f.calc_center_median().y, f.calc_center_median().x) < math.pi]
        fs.sort(key=lambda f: math.atan2(f.calc_center_median().y, f.calc_center_median().x))
        pat = "".join("Q" if len(f.verts) == 4 else "T" for f in fs)
        expect(pat == "QTQQQTQQQTQ", f"strip {z0}..{z1}: {pat} (expected QTQQQTQQQTQ)")


@test
def curve_selected_only_changes_just_that_curve():
    # Half of the top loop of a stacked cylinder: the parallel arcs below
    # form one band with it, but in selected mode only the picked arc
    # changes; triangles join it to the arc below. Going back to the original
    # count gives clean quads again. A bevel still changes as a whole.
    ob = make_stacked_cylinder(n=16, uvs=False)
    top = max(ZS)
    bm = edit(ob)
    sel = sorted((v for v in bm.verts if abs(v.co.z - top) < 1e-5), key=lambda v: math.atan2(v.co.y, v.co.x) % TAU)[:9]
    chosen = set(sel)
    select_edges(bm, lambda e: all(v in chosen for v in e.verts))
    bmesh.update_edit_mesh(ob.data)
    n_faces = len(bm.faces)
    expect(run_op(delta=4, whole_shape=False) == {'FINISHED'}, "op failed")
    bm = bmesh.from_edit_mesh(ob.data)
    got = ring_counts(bm)
    expect(got == [16, 16, 16, 16, 20],
           f"ring counts {got} (only the top ring should gain the 4 points)")
    expect(not check_topology(bm, expect_boundary=None), check_topology(bm, expect_boundary=None))
    expect(run_op(delta=-4, whole_shape=False) == {'FINISHED'}, "op failed")
    bm = bmesh.from_edit_mesh(ob.data)
    sizes = {len(f.verts) for f in bm.faces if max(v.co.z for v in f.verts) - min(v.co.z for v in f.verts) > 1e-5}
    expect(ring_counts(bm) == [16] * 5 and len(bm.faces) == n_faces and sizes == {4},
           f"back to 16: {ring_counts(bm)}, {len(bm.faces)} faces (was {n_faces}), side face sizes {sizes}")
    expect(not check_topology(bm, expect_boundary=None), check_topology(bm, expect_boundary=None))

    reset()
    ob = make_profile_cylinder([(1.0, -1.0)] + arc())
    bm = edit(ob)
    select_column(bm, 0.7)
    bmesh.update_edit_mesh(ob.data)
    run_op(delta=2, whole_shape=False)
    bm = bmesh.from_edit_mesh(ob.data)
    expect(len(bm.verts) == 16 * 7, f"verts {len(bm.verts)}: a bevel should still change all the way around")


@test
def curves_across_zigzag_strip_become_one_band():
    # Parallel arches with the same segment count, where the strip between
    # two of them is a zig-zag of triangles (what resampling two separate
    # groups leaves behind). Selected together they are one band, and the
    # zig-zag is rebuilt as clean quads.
    bm = bmesh.new()
    cols = []
    for off, y in ((0.0, 0.0), (0.05, 0.0), (0.1, 0.05), (0.2, 0.05)):
        r = 1.0 + off
        path = [(-r, -1.5)] + [(r * math.cos(math.pi - math.pi * i / 8), r * math.sin(math.pi - math.pi * i / 8))
                               for i in range(9)] + [(r, -1.5)]
        cols.append([bm.verts.new((x, y, z)) for x, z in path])
    for c in range(3):
        A, B = cols[c], cols[c + 1]
        for i in range(len(A) - 1):
            if c == 1 and i % 2:
                bm.faces.new((A[i], A[i + 1], B[i + 1]))
                bm.faces.new((A[i], B[i + 1], B[i]))
            elif c == 1:
                bm.faces.new((A[i], A[i + 1], B[i]))
                bm.faces.new((A[i + 1], B[i + 1], B[i]))
            else:
                bm.faces.new((A[i], A[i + 1], B[i + 1], B[i]))
    ob = new_object_from_bm(bm, "Arches")
    bm = edit(ob)
    n = len(cols[0])  # vertices were made column by column
    select_edges(bm, lambda e: e.verts[0].index // n == e.verts[1].index // n)
    bmesh.update_edit_mesh(ob.data)
    bands = curves.analyze(bmesh.from_edit_mesh(ob.data))
    expect(len(bands) == 1 and len(bands[0].columns) == 4, f"{[(b.m, len(b.columns)) for b in bands]}")
    run_op(delta=3)
    bm = bmesh.from_edit_mesh(ob.data)
    sizes = sorted({len(f.verts) for f in bm.faces})
    expect(sizes == [4] and len(bm.faces) == 3 * 13, f"face sizes {sizes}, {len(bm.faces)} faces (expected 39 quads)")
    expect(not check_topology(bm, expect_boundary=None), check_topology(bm, expect_boundary=None))


@test
def selected_round_trip_restores_clean_quads():
    # Change only the middle loop, confirm, then change it back: the bands
    # to the neighbours must be clean quads again, rails straight.
    # Single round trips, and several confirmed steps (which used to leave
    # crooked rails, triangles and even 5-sided faces).
    for steps in ((8,), (4,), (1,), (16,), (-4,), (-8,), (8, -4), (5, 3), (2, 2, 2), (-3, 9, -2)):
        delta = sum(steps)
        reset()
        ob = make_stacked_cylinder()
        bm = bmesh.from_edit_mesh(ob.data)
        original = sorted(tuple(round(c, 5) for c in v.co) for v in bm.verts)
        select_edges(bm, ring_pred(0.0))
        bmesh.update_edit_mesh(ob.data)
        for d in steps:
            run_selected(delta=d)
        bm = bmesh.from_edit_mesh(ob.data)
        expect(ring_counts(bm)[2] == 16 + delta, f"steps {steps}: {ring_counts(bm)}")
        run_selected(delta=-delta)
        bm = bmesh.from_edit_mesh(ob.data)
        expect(ring_counts(bm) == [16] * 5, f"steps {steps}: back {ring_counts(bm)}")
        sizes = sorted({len(f.verts) for f in bm.faces})
        expect(sizes == [4, 16], f"steps {steps}: face sizes after the round trip {sizes}")
        back = sorted(tuple(round(c, 5) for c in v.co) for v in bm.verts)
        expect(back == original, f"steps {steps}: vertices don't line up with the original")
        expect(not check_topology(bm), f"steps {steps}: {check_topology(bm)}")


@test
def selected_change_again_keeps_minimal_triangles():
    ob = make_stacked_cylinder()
    bm = bmesh.from_edit_mesh(ob.data)
    select_edges(bm, ring_pred(0.0))
    bmesh.update_edit_mesh(ob.data)
    run_selected(delta=8)   # 24
    run_selected(delta=-4)  # 20
    bm = bmesh.from_edit_mesh(ob.data)
    expect(ring_counts(bm) == [16, 16, 20, 16, 16], f"{ring_counts(bm)}")
    tris = sum(1 for f in bm.faces if len(f.verts) == 3)
    expect(tris == 8, f"{tris} triangles (expected 4 per band)")
    expect(not check_topology(bm), check_topology(bm))
    run_selected(delta=12)  # 32: twice the neighbours
    bm = bmesh.from_edit_mesh(ob.data)
    expect(ring_counts(bm) == [16, 16, 32, 16, 16], f"{ring_counts(bm)}")
    tris = sum(1 for f in bm.faces if len(f.verts) == 3)
    expect(tris == 32, f"{tris} triangles (expected 16 per band)")
    ring_xy = {(round(v.co.x, 5), round(v.co.y, 5)) for v in bm.verts if abs(v.co.z) < 1e-6}
    nb_xy = {(round(v.co.x, 5), round(v.co.y, 5)) for v in bm.verts if abs(v.co.z - 0.5) < 1e-6}
    expect(nb_xy <= ring_xy, "with twice the count every neighbour vertex should line up with a ring vertex")
    expect(not check_topology(bm), check_topology(bm))


@test
def select_similar_loops_across_objects():
    # Space / "Same-Count Loops": every round loop with the same count, in all
    # objects in edit mode, is resampled too.
    obs = {}
    for name, verts, loc, scale in (("A", 16, (0, 0, 0), (1, 1, 1)), ("B", 16, (4, 0, 0), (1, 1, 1)),
                                    ("C", 24, (8, 0, 0), (1, 1, 1)), ("D", 16, (12, 0, 0), (2, 1, 1))):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=verts, radius1=1, radius2=1, depth=2)
        bmesh.ops.scale(bm, vec=scale, verts=bm.verts)
        obs[name] = new_object_from_bm(bm, name)
        obs[name].location = loc
    for ob in obs.values():
        ob.select_set(True)
    bpy.context.view_layer.objects.active = obs["A"]
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    for ob in obs.values():
        bm = bmesh.from_edit_mesh(ob.data)
        for el in (*bm.verts, *bm.edges, *bm.faces):
            el.select = False
        if ob.name == "A":
            select_edges(bm, ring_pred(1.0))
        bmesh.update_edit_mesh(ob.data)
    expect(run_selected(delta=4, select_similar=True) == {'FINISHED'}, "op failed")
    got = {}
    for name, ob in obs.items():
        bm = bmesh.from_edit_mesh(ob.data)
        got[name] = (ring_count_z(bm, 1.0), ring_count_z(bm, -1.0))
        expect(not check_topology(bm), f"{name}: {check_topology(bm)}")
    expect(got == {"A": (20, 20), "B": (20, 20), "C": (24, 24), "D": (16, 16)},
           f"rings per object {got} (16-loops of round cylinders -> 20; the 24-gon and the ellipse untouched)")


# -----------------------------------------------------------------------------

failed = [r for r in RESULTS if not r[1]]
for name, ok, msg in RESULTS:
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        print("    " + msg.replace("\n", "\n    "))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed  (Blender {bpy.app.version_string})")
sys.stdout.flush()
os._exit(1 if failed else 0)
