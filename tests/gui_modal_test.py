"""End-to-end test of the interactive tool in a real Blender window, driven by
simulated input (Alt+T, mouse wheel, typing, Esc). Run with:

    blender --factory-startup --enable-event-simulate --python tests/gui_modal_test.py

Prints PASS/FAIL lines and quits by itself.
"""

import os
import sys
import traceback

import bpy
import bmesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import roundabout  # noqa: E402

roundabout.register()

LOG = []


def log(ok, msg):
    LOG.append(("PASS " if ok else "FAIL ") + msg)


def ring_count(z):
    ob = bpy.context.edit_object
    if ob is None:
        return -1
    bm = bmesh.from_edit_mesh(ob.data)
    return sum(1 for v in bm.verts if abs(v.co.z - z) < 0.2)


def top_ring_count():
    ob = bpy.context.edit_object
    if ob is None:
        return -1
    bm = bmesh.from_edit_mesh(ob.data)
    return sum(1 for v in bm.verts if abs(v.co.z - 1.0) < 1e-5)


def steps():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    x = area.x + area.width // 2
    y = area.y + area.height // 2

    def ev(t, v, **kw):
        win.event_simulate(type=t, value=v, x=x, y=y, **kw)

    UI = bpy.context.preferences.system.ui_scale

    def px(f):  # horizontal drag for a radius change of f (300 px = 100 %)
        return int(round(f * 300 * UI))

    # Scene: one 16-sided cylinder in edit mode, top ring selected.
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    bpy.ops.mesh.primitive_cylinder_add(vertices=16)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    ob = bpy.context.edit_object
    bm = bmesh.from_edit_mesh(ob.data)
    for e in bm.edges:
        e.select = all(abs(v.co.z - 1.0) < 1e-5 for v in e.verts)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    bpy.ops.ed.undo_push(message="test setup")
    yield 0.5

    for _ in range(3):
        ev('MOUSEMOVE', 'NOTHING')
        yield 0.2
    ev('ESC', 'PRESS')  # dismiss the factory-startup splash screen
    ev('ESC', 'RELEASE')
    yield 0.3
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    for _ in range(4):
        ev('WHEELUPMOUSE', 'PRESS')
        yield 0.05
    yield 0.2
    log(top_ring_count() == 20, f"wheel up x4 -> 20 segments (got {top_ring_count()})")

    for _ in range(9):
        ev('WHEELDOWNMOUSE', 'PRESS')
        yield 0.05
    yield 0.2
    log(top_ring_count() == 11, f"wheel down x9 -> 11 segments (got {top_ring_count()})")

    ev('WHEELUPMOUSE', 'PRESS', ctrl=True)
    yield 0.2
    log(top_ring_count() == 12, f"ctrl+wheel snaps to 12 (got {top_ring_count()})")

    ev('THREE', 'PRESS')
    ev('TWO', 'PRESS')
    yield 0.2
    log(top_ring_count() == 32, f"typing 32 (got {top_ring_count()})")

    ev('ESC', 'PRESS')
    yield 0.3
    log(top_ring_count() == 16, f"Esc restores 16 (got {top_ring_count()})")

    # Second session: change and confirm with Alt+T.
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    for _ in range(8):
        ev('WHEELUPMOUSE', 'PRESS')
        yield 0.05
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.3
    log(top_ring_count() == 24, f"Alt+T confirms 24 (got {top_ring_count()})")
    log(ring_count(-1.0) == 16, f"unselected bottom loop untouched (got {ring_count(-1.0)})")

    # Wheel after confirming must no longer resample.
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.3
    log(top_ring_count() == 24, f"wheel after toggle-off leaves mesh alone (got {top_ring_count()})")

    leftovers = [m.name for m in bpy.data.meshes if m.name.startswith("~roundabout")]
    log(not leftovers, f"snapshot meshes cleaned up {leftovers}")

    # Undo brings the original back.
    bpy.ops.ed.undo()
    yield 0.3
    log(top_ring_count() == 16, f"undo -> 16 (got {top_ring_count()})")

    # --- MMB: click fixes imperfect loops, drag still orbits ---------------------
    from roundabout import core

    def perfect():
        bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        rings = [r for c in core.analyze(bm, core.FIT_CIRCLE) for r in c.rings]
        return sum(core.ring_is_perfect(r) for r in rings), len(rings)

    def coords():
        bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        return [v.co.copy() for v in bm.verts]

    ob = bpy.context.edit_object
    bm = bmesh.from_edit_mesh(ob.data)
    for i, v in enumerate(bm.verts):
        if i % 3 == 0:
            v.co.x *= 1.06
            v.co.y *= 0.97
    bmesh.update_edit_mesh(ob.data)
    bpy.ops.ed.undo_push(message="dent")
    dented = coords()
    yield 0.3
    log(perfect()[0] == 0, f"setup: dented loops are not perfect {perfect()}")

    r3d = area.spaces.active.region_3d
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    ev('MIDDLEMOUSE', 'PRESS')
    ev('MIDDLEMOUSE', 'RELEASE')
    yield 0.3
    good, total = perfect()
    log(good == total == 1, f"MMB click fixes the selected loop ({good}/{total} perfect)")
    log(top_ring_count() == 16, f"MMB click keeps segment count (got {top_ring_count()})")
    fixed = coords()

    rot_before = r3d.view_rotation.copy()
    ev('MIDDLEMOUSE', 'PRESS')
    for i in range(1, 9):
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x + 12 * i, y=y + 5 * i)
        yield 0.03
    win.event_simulate(type='MIDDLEMOUSE', value='RELEASE', x=x + 96, y=y + 40)
    yield 0.3
    ev('MOUSEMOVE', 'NOTHING')
    yield 0.2
    angle = rot_before.rotation_difference(r3d.view_rotation).angle
    log(angle > 0.05, f"MMB drag orbits the view (rotated {angle:.3f} rad)")
    same = max((a - b).length for a, b in zip(coords(), fixed)) < 1e-6
    log(same, "MMB drag does not toggle the fix")

    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.3
    log(top_ring_count() == 17, f"tool still active after orbit: wheel -> 17 (got {top_ring_count()})")
    good, total = perfect()
    log(good == total, f"resampled loops stay perfect ({good}/{total})")

    ev('WHEELDOWNMOUSE', 'PRESS')
    ev('MIDDLEMOUSE', 'PRESS')
    ev('MIDDLEMOUSE', 'RELEASE')
    yield 0.3
    back = max((a - b).length for a, b in zip(coords(), dented)) < 1e-6
    log(back, "second MMB click turns the fix off (original shape back)")

    ev('MIDDLEMOUSE', 'PRESS')
    ev('MIDDLEMOUSE', 'RELEASE')
    yield 0.2
    ev('ESC', 'PRESS')
    yield 0.3
    back = max((a - b).length for a, b in zip(coords(), dented)) < 1e-6
    log(back, "Esc discards the fix")

    # W switches to whole-shape mode: the bottom loop follows.
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.2
    log((ring_count(1.0), ring_count(-1.0)) == (17, 16), f"selected loops only: {ring_count(1.0)}, {ring_count(-1.0)}")
    ev('W', 'PRESS')
    yield 0.3
    log((ring_count(1.0), ring_count(-1.0)) == (17, 17), f"W -> whole shape: {ring_count(1.0)}, {ring_count(-1.0)}")
    ev('W', 'PRESS')
    yield 0.3
    log((ring_count(1.0), ring_count(-1.0)) == (17, 16), f"W again -> selected loops: {ring_count(1.0)}, {ring_count(-1.0)}")
    ev('ESC', 'PRESS')
    yield 0.3
    # The W choice is remembered for the next Alt+T, until W is pressed again.
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    ev('W', 'PRESS')
    yield 0.2
    ev('ESC', 'PRESS')
    yield 0.3
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.2
    log((ring_count(1.0), ring_count(-1.0)) == (17, 17), f"next Alt+T starts in whole shape: {ring_count(1.0)}, {ring_count(-1.0)}")
    ev('W', 'PRESS')
    yield 0.2
    ev('ESC', 'PRESS')
    yield 0.3
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.2
    log((ring_count(1.0), ring_count(-1.0)) == (17, 16), f"after W again it starts in selected loops: {ring_count(1.0)}, {ring_count(-1.0)}")
    ev('ESC', 'PRESS')
    yield 0.3
    # Space adds the other loop with the same count; Esc drops it again.
    def selected_bottom():
        bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        return sum(1 for e in bm.edges if e.select and all(abs(v.co.z + 1.0) < 0.2 for v in e.verts))
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    ev('SPACE', 'PRESS')
    ev('SPACE', 'RELEASE')
    yield 0.3
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.3
    log((ring_count(1.0), ring_count(-1.0)) == (17, 17), f"Space adds the same-count loop: {ring_count(1.0)}, {ring_count(-1.0)}")
    log(bool(list(win.modal_operators)), "Space no longer applies")
    ev('ESC', 'PRESS')
    yield 0.3
    log((ring_count(1.0), ring_count(-1.0)) == (16, 16) and selected_bottom() == 0,
        f"Esc restores counts and selection ({ring_count(1.0)}, {ring_count(-1.0)}, {selected_bottom()} bottom edges selected)")
    # --- LMB drag scales the selected loop, a click applies ----------------------
    from bpy_extras import view3d_utils
    from mathutils import Vector
    region = next(r for r in area.regions if r.type == 'WINDOW')

    def radius(z):
        bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        rs = [v.co.xy.length for v in bm.verts if abs(v.co.z - z) < 1e-5]
        return sum(rs) / len(rs)

    c2d = view3d_utils.location_3d_to_region_2d(region, r3d, Vector((0, 0, 1)))
    sx, sy = int(region.x + c2d.x + 100), int(region.y + c2d.y)
    r0, b0 = radius(1.0), radius(-1.0)
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx, y=sy)
    yield 0.05
    win.event_simulate(type='LEFTMOUSE', value='PRESS', x=sx, y=sy)
    for i in range(1, 9):
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx + px(0.8) * i // 8, y=sy)
        yield 0.03
    win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=sx + px(0.8), y=sy)
    yield 0.3
    r1 = radius(1.0)
    log(abs(r1 / r0 - 1.8) < 0.05, f"LMB drag scales the loop radius x1.8 ({r0:.3f} -> {r1:.3f})")
    log(abs(radius(-1.0) - b0) < 1e-6, "LMB drag leaves unselected loops alone")
    win.event_simulate(type='WHEELUPMOUSE', value='PRESS', x=sx + px(0.8), y=sy)
    yield 0.3
    log(top_ring_count() == 17, f"tool still active after the drag: wheel -> 17 (got {top_ring_count()})")
    win.event_simulate(type='LEFTMOUSE', value='PRESS', x=sx + px(0.8), y=sy)
    win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=sx + px(0.8), y=sy)
    yield 0.3
    win.event_simulate(type='WHEELUPMOUSE', value='PRESS', x=sx + px(0.8), y=sy)
    yield 0.3
    log(top_ring_count() == 17 and abs(radius(1.0) / r0 - 1.8) < 0.05,
        f"LMB click applies ({top_ring_count()} segments, radius {radius(1.0):.3f})")

    # --- open curve: a bevel profile ---------------------------------------------
    import math
    bpy.ops.object.mode_set(mode='OBJECT')
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    n, w = 16, 0.3
    prof = [(1.0, -1.0)] + [(1 - w + w * math.cos(math.radians(a)), 1 - w + w * math.sin(math.radians(a)))
                            for a in (0, 30, 60, 90)]
    bm = bmesh.new()
    rings = [[bm.verts.new((r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), z))
              for k in range(n)] for r, z in prof]
    for A, B in zip(rings, rings[1:]):
        for k in range(n):
            bm.faces.new((A[k], A[(k + 1) % n], B[(k + 1) % n], B[k]))
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    me = bpy.data.meshes.new("Bevel")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("Bevel", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(ob.data)
    for e in bm.edges:
        e.select = all(abs(v.co.y) < 1e-6 and v.co.x > 0 and v.co.z > 0.7 - 1e-6 for v in e.verts)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    bpy.ops.ed.undo_push(message="bevel setup")
    yield 0.3

    def column():
        bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        return sorted((v.co.copy() for v in bm.verts
                       if abs(v.co.y) < 1e-6 and v.co.x > 0.3 and v.co.z > 0.3),
                      key=lambda p: p.z)

    def nverts():
        return len(bmesh.from_edit_mesh(bpy.context.edit_object.data).verts)

    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    for _ in range(3):
        ev('WHEELUPMOUSE', 'PRESS')
        yield 0.05
    yield 0.2
    log(nverts() == 16 * 8, f"curve: wheel x3 -> 6 bevel segments ({nverts()} verts)")
    round_col = column()
    # LMB drag: radius. The bevel scales about its corner (1, 0, 1).
    c2d = view3d_utils.location_3d_to_region_2d(region, r3d, Vector((0, 0, 1)))
    sx, sy = int(region.x + c2d.x + 100), int(region.y + c2d.y)
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx, y=sy)
    yield 0.05
    win.event_simulate(type='LEFTMOUSE', value='PRESS', x=sx, y=sy)
    for i in range(1, 9):
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx + px(0.8) * i // 8, y=sy)
        yield 0.03
    win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=sx + px(0.8), y=sy)
    yield 0.3
    big = column()
    width = 1.0 - big[-1].x
    log(abs(width - 0.54) < 0.02, f"curve: LMB drag scales the bevel x1.8 (width 0.30 -> {width:.3f})")
    log(nverts() == 16 * 8, "curve: radius drag keeps the segment count")
    # RMB drag: tension.
    ev('RIGHTMOUSE', 'PRESS')
    for i in range(1, 9):
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x + 15 * i, y=y)
        yield 0.03
    win.event_simulate(type='RIGHTMOUSE', value='RELEASE', x=x + 120, y=y)
    yield 0.3
    tight = column()
    corner = Vector((1, 0, 1))
    d_round = sum((p - corner).length for p in big[1:-1])
    d_tight = sum((p - corner).length for p in tight[1:-1])
    log(d_tight < d_round * 0.9, f"curve: RMB drag tightens the profile ({d_round:.3f} -> {d_tight:.3f})")
    log(abs((1.0 - tight[-1].x) - width) < 1e-6, "curve: RMB drag keeps the radius and does not cancel")
    ev('MIDDLEMOUSE', 'PRESS')
    ev('MIDDLEMOUSE', 'RELEASE')
    yield 0.3
    even = column()
    gaps = [(b - a).length for a, b in zip(even, even[1:])]
    log(max(gaps) - min(gaps) < 1e-3 * sum(gaps), f"curve: MMB click spaces points evenly {[round(g, 4) for g in gaps]}")
    ev('LEFTMOUSE', 'PRESS')
    ev('LEFTMOUSE', 'RELEASE')
    yield 0.3
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.3
    log(nverts() == 16 * 8, f"curve: LMB click applies ({nverts()} verts)")
    # Infinite drag: crossing the right edge wraps the cursor to the left edge
    # and the drag keeps counting.
    before = column()
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    op = list(win.modal_operators)[0]
    base = op._profile
    right = region.x + region.width
    x0 = right - 60
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x0, y=y)
    yield 0.05
    win.event_simulate(type='RIGHTMOUSE', value='PRESS', x=x0, y=y)
    for k in range(1, 4):  # the last one reaches the edge
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x0 + 20 * k, y=y)
        yield 0.03
    nx = region.x + 6
    for j in range(1, 6):  # the cursor now comes in from the left
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=nx + 20 * j, y=y)
        yield 0.03
    shift = op.profile_shift
    win.event_simulate(type='RIGHTMOUSE', value='RELEASE', x=nx + 100, y=y)
    yield 0.2
    ui = bpy.context.preferences.system.ui_scale
    want = min(160 / (400 * ui), 1.0)
    log(abs(shift - want) < 0.01, f"infinite drag: tension keeps counting across the edge (shift {shift:.3f}, want {want:.3f})")
    ev('ESC', 'PRESS')
    yield 0.3
    log(max((a - b).length for a, b in zip(column(), before)) < 1e-6, "infinite drag: Esc restores")
    applied = column()
    # RMB click cancels.
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.2
    changed = nverts()
    ev('RIGHTMOUSE', 'PRESS')
    ev('RIGHTMOUSE', 'RELEASE')
    yield 0.3
    same = len(column()) == len(applied) and max((a - b).length for a, b in zip(column(), applied)) < 1e-6
    log(changed == 16 * 9 and nverts() == 16 * 8 and same, f"curve: RMB click cancels ({changed} -> {nverts()})")
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.3
    log(nverts() == 16 * 8, "curve: tool is off after RMB cancel")
    # LMB drag toward the center stops at 0: the bevel merges into a sharp edge.
    bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
    for e in bm.edges:
        e.select = False
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = all(abs(v.co.y) < 1e-6 and v.co.x > 0 and v.co.z > 0.3 and v.co.x > 0.3 for v in e.verts)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(bpy.context.edit_object.data)
    yield 0.2
    c2d = view3d_utils.location_3d_to_region_2d(region, r3d, Vector((0, 0, 1)))
    sx, sy = int(region.x + c2d.x + 100), int(region.y + c2d.y)
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx, y=sy)
    yield 0.05
    win.event_simulate(type='LEFTMOUSE', value='PRESS', x=sx, y=sy)
    for i in range(1, 13):
        win.event_simulate(type="MOUSEMOVE", value="NOTHING", x=sx - px(1.2) * i // 12, y=sy)  # well past the stop point
        yield 0.03
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx - px(1.2), y=sy)
    yield 0.05
    win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=sx - px(1.2), y=sy)
    yield 0.3
    log(nverts() == 32, f"curve: dragging the radius to the stop point merges the bevel ({nverts()} verts)")
    # Still in the tool: a new drag from the collapsed state grows it back.
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx, y=sy)
    yield 0.05
    win.event_simulate(type='LEFTMOUSE', value='PRESS', x=sx, y=sy)
    for i in range(1, 11):
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx + px(1.0) * i // 10, y=sy)
        yield 0.03
    win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=sx + px(1.0), y=sy)
    yield 0.3
    log(nverts() == 16 * 8, f"curve: dragging again after collapsing brings it back ({nverts()} verts)")
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx, y=sy)
    yield 0.05
    win.event_simulate(type='LEFTMOUSE', value='PRESS', x=sx, y=sy)
    for i in range(1, 13):
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=sx - px(1.2) * i // 12, y=sy)
        yield 0.03
    win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=sx - px(1.2), y=sy)
    yield 0.3
    log(nverts() == 32, f"curve: and collapses again ({nverts()} verts)")
    win.event_simulate(type='LEFTMOUSE', value='PRESS', x=sx - px(1.2), y=sy)
    win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=sx - px(1.2), y=sy)
    yield 0.3
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.3
    log(nverts() == 32, f"curve: the merge is kept after confirming ({nverts()} verts)")
    # Alt+T on the collapsed edge restores the bevel: drag outward from radius 0.
    def drag_lmb(x0, y0, x1, y1, steps=10):
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x0, y=y0)
        yield 0.05
        win.event_simulate(type='LEFTMOUSE', value='PRESS', x=x0, y=y0)
        for i in range(1, steps + 1):
            win.event_simulate(type='MOUSEMOVE', value='NOTHING',
                               x=int(x0 + (x1 - x0) * i / steps), y=int(y0 + (y1 - y0) * i / steps))
            yield 0.03
        win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x1, y=y1)
        yield 0.3

    def click_lmb(x0, y0):
        win.event_simulate(type='LEFTMOUSE', value='PRESS', x=x0, y=y0)
        win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x0, y=y0)

    c2d = view3d_utils.location_3d_to_region_2d(region, r3d, Vector((0, 0, 1)))
    sx, sy = int(region.x + c2d.x + 100), int(region.y + c2d.y)  # the view may have zoomed
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    yield from drag_lmb(sx, sy, sx + px(1.0), sy)  # 300 px right from 0: original size
    log(nverts() == 16 * 8, f"restore: LMB drag brings the bevel back ({nverts()} verts)")
    click_lmb(sx + px(1.0), sy)
    yield 0.3
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.3
    log(nverts() == 16 * 8, f"restore: LMB click applies ({nverts()} verts)")

    # Collapse again, then leave and re-enter edit mode: the collapse is forgotten.
    c2d = view3d_utils.location_3d_to_region_2d(region, r3d, Vector((0, 0, 1)))
    sx, sy = int(region.x + c2d.x + 100), int(region.y + c2d.y)  # the view may have zoomed
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    yield from drag_lmb(sx, sy, sx - px(1.2), sy, steps=12)
    click_lmb(sx - px(1.2), sy)
    yield 0.3
    log(nverts() == 32, f"collapsed again ({nverts()} verts)")
    # Restore on the first try, wherever the drag starts: right always grows.
    for corner_x, corner_y in ((region.x + 150, region.y + region.height - 150),
                               (region.x + region.width - 400, region.y + 150)):
        ev('T', 'PRESS', alt=True)
        ev('T', 'RELEASE', alt=True)
        yield 0.2
        yield from drag_lmb(corner_x, corner_y, corner_x + px(0.5), corner_y + 40)
        log(nverts() == 16 * 8, f"restore: first drag right from ({corner_x}, {corner_y}) expands ({nverts()} verts)")
        yield from drag_lmb(corner_x + px(0.5), corner_y + 40, corner_x + px(0.5) - px(1.1), corner_y)  # 300 px left always reaches 0
        log(nverts() == 32, f"restore: drag left shrinks back to the stop point ({nverts()} verts)")
        ev('ESC', 'PRESS')
        yield 0.3
    ev('TAB', 'PRESS')
    ev('TAB', 'RELEASE')
    yield 0.4
    ev('TAB', 'PRESS')
    ev('TAB', 'RELEASE')
    yield 0.4
    log(bpy.context.mode == 'EDIT_MESH', f"back in edit mode ({bpy.context.mode})")
    c2d = view3d_utils.location_3d_to_region_2d(region, r3d, Vector((0, 0, 1)))
    sx, sy = int(region.x + c2d.x + 100), int(region.y + c2d.y)  # the view may have zoomed
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    yield from drag_lmb(sx, sy, sx + px(1.0), sy)
    log(nverts() == 32, f"after leaving edit mode Alt+T no longer restores ({nverts()} verts)")
    ev('ESC', 'PRESS')
    yield 0.3
    leftovers = [m.name for m in bpy.data.meshes if m.name.startswith("~roundabout")]
    log(not leftovers, f"snapshot meshes cleaned up after curve mode {leftovers}")

    # --- mixed counts: Space only selects; E / D match the counts -----------------
    bpy.ops.object.mode_set(mode='OBJECT')
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bm = bmesh.new()
    for segs, dx in ((16, -1.5), (24, 1.5)):
        geom = bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=1, radius2=1, depth=2)
        bmesh.ops.translate(bm, vec=(dx, 0, 0), verts=geom["verts"])
    me = bpy.data.meshes.new("Mixed")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("Mixed", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(me)
    for e in bm.edges:
        e.select = all(abs(v.co.z - 1.0) < 1e-5 for v in e.verts)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(me)
    bpy.ops.ed.undo_push(message="mixed setup")
    yield 0.3

    def counts():
        bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        out = []
        for side in (-1, 1):
            for z in (1.0, -1.0):
                out.append(sum(1 for v in bm.verts if abs(v.co.z - z) < 1e-5 and v.co.x * side > 0))
        return tuple(out)  # (left top, left bottom, right top, right bottom)

    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    ev('SPACE', 'PRESS')
    ev('SPACE', 'RELEASE')
    yield 0.3
    log(counts() == (16, 16, 24, 24), f"mixed counts: Space only selects, counts unchanged {counts()}")
    ev('WHEELUPMOUSE', 'PRESS')
    yield 0.3
    log(counts() == (17, 17, 25, 25), f"mixed counts: wheel changes each by one {counts()}")
    ev('E', 'PRESS')
    yield 0.3
    log(counts() == (25, 25, 25, 25), f"E matches the highest {counts()}")
    ev('D', 'PRESS')
    yield 0.3
    log(counts() == (25, 25, 25, 25), f"D right after E: all equal already {counts()}")
    ev('ESC', 'PRESS')
    yield 0.3
    ev('T', 'PRESS', alt=True)
    ev('T', 'RELEASE', alt=True)
    yield 0.2
    ev('D', 'PRESS')
    yield 0.3
    # only the top loops are selected now (Esc dropped what Space added)
    log(counts() == (16, 16, 16, 24), f"D matches the lowest selected {counts()}")
    ev('ESC', 'PRESS')
    yield 0.3
    log(counts() == (16, 16, 24, 24), f"Esc restores the mixed counts {counts()}")



def driver(gen=[]):
    if not gen:
        gen.append(steps())
    try:
        return next(gen[0])
    except StopIteration:
        pass
    except Exception:  # noqa: BLE001
        LOG.append("FAIL exception\n" + traceback.format_exc())
    print("\n".join(LOG))
    print(f"GUI: {sum(l.startswith('PASS') for l in LOG)}/{len(LOG)} passed")
    sys.stdout.flush()
    os._exit(0 if all(l.startswith("PASS") for l in LOG) else 1)


bpy.app.timers.register(driver, first_interval=1.0)
