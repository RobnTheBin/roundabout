# SPDX-FileCopyrightText: 2026 Robin Mariančík
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive resample operator: Alt+T toggles it, mouse wheel changes the
segment count, LMB drag changes the radius, RMB drag the tension of open
curves. A plain LMB click (or Alt+T) applies, a plain RMB click cancels."""

import math

import bpy
import blf
import bmesh
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from . import core, curves, restore

KIND_RINGS = 'RINGS'
KIND_CURVES = 'CURVES'
KIND_RESTORE = 'RESTORE'  # a curve collapsed earlier in this edit session

FIT_ITEMS = (
    (core.FIT_AUTO, "Auto", "Loops: perfect circle for round rings, keep the profile for others. "
     "Curves: exact profile when the curve is a clean bevel profile, otherwise a spline"),
    (core.FIT_CIRCLE, "Circle", "Loops: make every ring a perfect, evenly spaced circle (best fit, so "
     "vertices move as little as possible). Curves: snap to the exact best-fit bevel profile"),
    (core.FIT_SPLINE, "Keep Profile", "Follow a smooth curve through the original vertices "
     "(for elliptical or irregular sections)"),
)
FIT_LABELS = {k: label for k, label, _ in FIT_ITEMS}
CURVE_FIT_LABELS = {core.FIT_AUTO: "Auto", core.FIT_CIRCLE: "Exact Profile", core.FIT_SPLINE: "Spline"}
FIT_CYCLE = [k for k, _, _ in FIT_ITEMS]

DIGITS = {
    'ZERO': '0', 'ONE': '1', 'TWO': '2', 'THREE': '3', 'FOUR': '4',
    'FIVE': '5', 'SIX': '6', 'SEVEN': '7', 'EIGHT': '8', 'NINE': '9',
    'NUMPAD_0': '0', 'NUMPAD_1': '1', 'NUMPAD_2': '2', 'NUMPAD_3': '3', 'NUMPAD_4': '4',
    'NUMPAD_5': '5', 'NUMPAD_6': '6', 'NUMPAD_7': '7', 'NUMPAD_8': '8', 'NUMPAD_9': '9',
}
PASS_THROUGH = {
    'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM',
    'WINDOW_DEACTIVATE', 'TIMER', 'TIMER_REPORT', 'TIMERREGION',
}
INCREASE = {'WHEELUPMOUSE', 'NUMPAD_PLUS', 'EQUAL', 'UP_ARROW', 'RIGHT_ARROW'}
DECREASE = {'WHEELDOWNMOUSE', 'NUMPAD_MINUS', 'MINUS', 'DOWN_ARROW', 'LEFT_ARROW'}
CONFIRM = {'RET', 'NUMPAD_ENTER'}
CANCEL = {'ESC'}

STATUS_RINGS = (
    "Wheel / +-: segments    Ctrl+Wheel: ×4    0-9: type    LMB drag: radius (Ctrl: snap)    "
    "MMB: perfect circle    W: selected / whole    R: tidy    Space: select similar    "
    "E / D: match max / min    F: fit    LMB / Enter: apply    Esc / RMB: cancel"
)
STATUS_CURVES = (
    "Wheel / +-: segments    Ctrl+Wheel: ×4    0-9: type    LMB drag: radius    RMB drag: tension    "
    "(Ctrl: snap)    MMB: even spacing    W: selected / whole    R: tidy    E / D: match max / min    "
    "F: fit    LMB / Enter: apply    Esc / RMB: cancel"
)
STATUS_RESTORE = (
    "LMB drag: restore bevel    RMB drag: tension    Wheel: segments    "
    "LMB / Enter: apply    Esc / RMB: cancel"
)
RADIUS_DRAG = 300.0   # px of horizontal drag per 100 % radius change
COLLAPSE_SNAP = 0.04  # curve radius factor below which the drag stops at 0 (merge)
NO_SELECTION = "Select closed ring loops, or an open edge path such as a bevel profile"


class _SessionPrefs:
    """Stand-in when the add-on preferences aren't available (e.g. loaded as
    a plain module): keeps the choices for the Blender session."""
    default_whole_shape = False


def _prefs(context):
    addon = context.preferences.addons.get(__package__)
    if addon is not None and hasattr(addon.preferences, "default_whole_shape"):
        return addon.preferences
    return _SessionPrefs


def _mesh_objects(context):
    obs = getattr(context, "objects_in_mode_unique_data", None) or [context.edit_object]
    return [ob for ob in obs if ob is not None and ob.type == 'MESH']


def _blf_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:  # Blender < 4.0
        blf.size(font_id, int(size), 72)


def _select_similar(objects):
    """Select all other closed loops with the same vertex count as the
    selected loops (round ones only, if every selected loop is round) in all
    given objects. Returns the number of loops added."""
    counts = set()
    round_all = True
    for ob in objects:
        bm = bmesh.from_edit_mesh(ob.data)
        for verts in core.extract_selected_rings(bm):
            ring = core.Ring(verts)
            if not ring.valid:
                continue
            ring.fit()
            counts.add(ring.n)
            round_all = round_all and ring.circle_ok
    added = 0
    for ob in objects:
        bm = bmesh.from_edit_mesh(ob.data)
        n = core.select_similar_loops(bm, counts, round_all)
        if n:
            bm.select_flush_mode()
            bmesh.update_edit_mesh(ob.data)
            added += n
    return added


def _find_kind(objects):
    """A remembered collapse wins, then loops; open curves are only used when
    no closed loop is selected."""
    restore.purge()
    for ob in objects:
        if restore.find(ob.data, bmesh.from_edit_mesh(ob.data)):
            return KIND_RESTORE
    has_curves = False
    for ob in objects:
        bm = bmesh.from_edit_mesh(ob.data)
        if core.analyze(bm, whole=True):
            return KIND_RINGS
        if not has_curves and curves.analyze(bm):
            has_curves = True
    return KIND_CURVES if has_curves else None


class MESH_OT_roundabout(bpy.types.Operator):
    """Change the number of segments of the selected ring loops, or of a selected open curve (such as a bevel profile).
With Whole Shape, every ring connected through quad strips (bevels, insets, inner walls) is resampled too"""

    bl_idname = "mesh.roundabout"
    bl_label = "Roundabout"
    bl_options = {'REGISTER', 'UNDO'}

    delta: IntProperty(
        name="Segment Change",
        description="Segments added (positive) or removed (negative) relative to each shape's original count",
        default=0, min=-4096, max=4096,
    )
    use_absolute: BoolProperty(
        name="Set Exact Count",
        description="Give every selected shape the same segment count instead of a relative change",
        default=False,
    )
    segments: IntProperty(
        name="Segments",
        description="Segment count used when 'Set Exact Count' is enabled",
        default=32, min=curves.MIN_SEGMENTS, max=core.MAX_SEGMENTS,
    )
    fit_mode: EnumProperty(name="Fit", items=FIT_ITEMS, default=core.FIT_AUTO)
    whole_shape: BoolProperty(
        name="Whole Shape",
        description="Resample every loop connected to the selection through quad strips (side, bevels, "
                    "insets, inner wall) so the shape keeps all-quad topology. When off, only the selected "
                    "loops change and are joined to their neighbours with quads and triangles. On open curves: "
                    "when off, only the selected curves change, except across a bevel, which always changes "
                    "as a whole",
        default=False,
    )
    radius_scale: FloatProperty(
        name="Radius",
        description="Scale the radius (1 = unchanged). Loops scale about their own centers; a bevel "
                    "profile scales about its corner, so its ends slide along the faces it joins; "
                    "an arc scales about its circle center. On curves the factor applies to the selected "
                    "curve and the rest of its band gets the same absolute change, so parallel loops "
                    "(e.g. a bevel wrapping around its end) stay parallel. 0 on a curve merges every "
                    "column into one point, which removes a bevel",
        default=1.0, min=0.0, max=1000.0, soft_min=0.0, soft_max=10.0,
    )
    profile_shift: FloatProperty(
        name="Tension",
        description="Open curves: make the curve tighter (positive, toward a sharp corner) or flatter "
                    "(negative, toward a straight chamfer). The profile value works like Bevel's Profile: "
                    "0.25 straight, 0.5 round, 1 square",
        default=0.0, min=-1.0, max=1.0, step=1,
    )
    even_spacing: BoolProperty(
        name="Even Spacing",
        description="Open curves: distribute the points evenly along the curve",
        default=False,
    )
    tidy: BoolProperty(
        name="Tidy",
        description="Rebuild the strips that join the changed loops or curves to their neighbours with an "
                    "even pattern: quads where the counts match (the loop turns to line up), and where they "
                    "differ, the triangles spread evenly and symmetrically (R while resampling)",
        default=False,
    )
    select_similar: BoolProperty(
        name="Select Similar",
        description="Also resample every other closed loop with the same vertex count in the objects "
                    "being edited (Space while resampling)",
        default=False,
    )
    kind: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    # -- shared helpers --------------------------------------------------------------

    def _targets(self, orig):
        if self.use_absolute:
            return [self.segments] * len(orig)
        return [n + self.delta for n in orig]

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "use_absolute")
        if self.use_absolute:
            layout.prop(self, "segments")
        else:
            layout.prop(self, "delta")
        layout.prop(self, "fit_mode")
        layout.prop(self, "radius_scale")
        if self.kind != KIND_RESTORE:
            layout.prop(self, "tidy")
        if self.kind == KIND_CURVES:
            layout.prop(self, "profile_shift")
            layout.prop(self, "even_spacing")
        elif self.kind == KIND_RESTORE:
            layout.prop(self, "profile_shift")
        else:
            layout.prop(self, "whole_shape")
            layout.prop(self, "select_similar")

    # -- non-interactive (redo panel, F3 search, scripts) -------------------------

    def execute(self, context):
        objects = _mesh_objects(context)
        if self.select_similar and _find_kind(objects) == KIND_RINGS:
            _select_similar(objects)
        kind = _find_kind(objects)
        if kind is None:
            self.report({'ERROR'}, NO_SELECTION)
            return {'CANCELLED'}
        self.kind = kind
        force = self.fit_mode != core.FIT_AUTO
        for ob in objects:
            me = ob.data
            if me.shape_keys:
                self.report({'WARNING'}, f"'{ob.name}' has shape keys, skipped")
                continue
            bm = bmesh.from_edit_mesh(me)
            if kind == KIND_RESTORE:
                matches = restore.find(me, bm)
                if not matches:
                    continue
                restore.expand(bm, matches, self.radius_scale,
                               self._targets([r.segments for r, _ in matches]), self.profile_shift)
            elif kind == KIND_RINGS:
                chains = core.analyze(bm, self.fit_mode, whole=self.whole_shape)
                if not chains:
                    continue
                core.resample(bm, chains, self._targets([c.n for c in chains]),
                              force=force, scale=self.radius_scale, tidy=self.tidy)
            else:
                bands = curves.analyze(bm, self.fit_mode, whole=self.whole_shape)
                if not bands:
                    continue
                targets = self._targets([b.m for b in bands])
                curves.resample(bm, bands, targets,
                                even=self.even_spacing, shift=self.profile_shift, force=force,
                                scale=self.radius_scale, tidy=self.tidy)
                if self.radius_scale == 0.0:
                    restore.remember(me, _collapse_records(bands, targets, self.profile_shift))
            bmesh.update_edit_mesh(me)
        return {'FINISHED'}

    # -- interactive ---------------------------------------------------------------

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            return self.execute(context)

        objects = _mesh_objects(context)
        kind = _find_kind(objects)
        if kind is None:
            self.report({'ERROR'}, NO_SELECTION)
            return {'CANCELLED'}
        self.kind = kind

        skipped = self._collect_states(objects, kind)
        if skipped:
            self.report({'WARNING'}, "Shape keys are not supported, skipped: " + ", ".join(skipped))
        if not self._states:
            return {'CANCELLED'}

        self.whole_shape = _prefs(context).default_whole_shape
        if kind == KIND_RINGS and not any(st["rings"][self.whole_shape] for st in self._states):
            self.whole_shape = not self.whole_shape
        self._update_stats()

        self.delta = 0
        self.use_absolute = False
        self._match_base = None  # counts E / D match against (before the first E / D)
        self.segments = self._primary
        self.fit_mode = core.FIT_AUTO
        self._initial_radius = 0.0 if kind == KIND_RESTORE else 1.0
        self.radius_scale = self._initial_radius
        self.profile_shift = 0.0
        self.even_spacing = False
        self.tidy = False
        self.select_similar = False
        self._similar_added = None
        self._first_snaps = {}  # mesh pointer -> (mesh, snapshot) from before the first Space
        self._typed = ""
        self._mouse = (event.mouse_region_x, event.mouse_region_y)
        self._region = context.region
        self._mmb = None
        self._press = None  # LMB / RMB press that may become a drag
        self._prev_fit = core.FIT_AUTO
        self._fixed = None  # (loops that needed fixing, loops checked)

        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_hud, (self,), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self._update_ui(context)
        return {'RUNNING_MODAL'}

    def _collect_states(self, objects, kind):
        """Per object: what will be resampled, plus a snapshot of the mesh.
        Returns the names of objects skipped for having shape keys."""
        self._states = []
        skipped = []
        self._profile = None
        self._has_radius = kind != KIND_CURVES
        for ob in objects:
            me = ob.data
            bm = bmesh.from_edit_mesh(me)
            st = {"mesh": me}
            if kind == KIND_RESTORE:
                matches = restore.find(me, bm)
                if not matches:
                    continue
                st["matches"] = matches
                st["curves"] = [r.segments for r, _ in matches]
                if self._profile is None:
                    self._profile = matches[0][0].profile
            elif kind == KIND_RINGS:
                st["rings"] = {whole: [c.n for c in core.analyze(bm, whole=whole)] for whole in (False, True)}
                if not st["rings"][True]:
                    continue
            else:
                bands = curves.analyze(bm)
                st["curves"] = [b.m for b in bands]
                st["curves_w"] = {False: [b.m for b in curves.analyze(bm, whole=False)], True: st["curves"]}
                st["cache"] = {}
                if not bands:
                    continue
                if self._profile is None:
                    self._profile = bands[0].profile
                self._has_radius = self._has_radius or any(b.has_radius() for b in bands)
            if me.shape_keys:
                skipped.append(ob.name)
                continue
            snap = bpy.data.meshes.new("~roundabout_snapshot")
            bm.to_mesh(snap)
            st["snap"] = snap
            self._states.append(st)
        return skipped

    def _add_similar(self, context):
        """Space: add every loop with the same count (all objects in edit
        mode) and keep resampling with the current settings."""
        for st in self._states:
            me = st["mesh"]
            bm = bmesh.from_edit_mesh(me)
            select_mode = set(bm.select_mode)
            bm.clear()
            bm.from_mesh(st["snap"])
            bm.select_mode = select_mode
            bmesh.update_edit_mesh(me)
        objects = _mesh_objects(context)
        for ob in objects:
            key = ob.data.as_pointer()
            if key not in self._first_snaps:
                snap = bpy.data.meshes.new("~roundabout_original")
                bmesh.from_edit_mesh(ob.data).to_mesh(snap)
                self._first_snaps[key] = (ob.data, snap)
        added = _select_similar(objects)
        self._similar_added = (self._similar_added or 0) + added
        self.select_similar = True
        if added:
            for st in self._states:
                snap = st.pop("snap", None)
                if snap is not None:
                    bpy.data.meshes.remove(snap)
            # Only the selection grows: the change (relative or exact) stays as it is.
            self._collect_states(objects, self.kind)
            self._update_stats()
        self._apply(context)

    def _orig(self, st):
        if self.kind == KIND_RINGS:
            return st["rings"][self.whole_shape]
        if self.kind == KIND_CURVES:
            return st["curves_w"][self.whole_shape]
        return st["curves"]  # curves, or the segment counts of remembered collapses

    @property
    def _min_segments(self):
        return core.MIN_SEGMENTS if self.kind == KIND_RINGS else curves.MIN_SEGMENTS

    @property
    def _max_segments(self):
        return core.MAX_SEGMENTS if self.kind == KIND_RINGS else curves.MAX_SEGMENTS

    def _update_stats(self):
        all_orig = [n for st in self._states for n in self._orig(st)]
        self._primary = all_orig[0]
        self._min_orig = min(all_orig)
        self._max_orig = max(all_orig)
        self._shape_count = len(all_orig)
        self._uniform = len(set(all_orig)) == 1

    def _current(self):
        return self.segments if self.use_absolute else self._primary + self.delta

    def _set_primary(self, value):
        if self.use_absolute:
            self.segments = max(self._min_segments, min(self._max_segments, value))
        else:
            lo = self._min_segments - self._min_orig
            hi = self._max_segments - self._max_orig
            self.delta = max(lo, min(hi, value - self._primary))

    def _step(self, direction, snap):
        self._typed = ""
        self._match_base = None
        cur = self._current()
        if snap:
            target = (cur // 4 + 1) * 4 if direction > 0 else ((cur - 1) // 4) * 4
            target = max(target, 4)
        else:
            target = cur + direction
        self._set_primary(target)

    def _apply(self, context):
        bad = checked = 0
        force = self.fit_mode != core.FIT_AUTO
        for st in self._states:
            me = st["mesh"]
            bm = bmesh.from_edit_mesh(me)
            select_mode = set(bm.select_mode)
            bm.clear()
            bm.from_mesh(st["snap"])
            bm.select_mode = select_mode
            orig = self._orig(st)
            if not orig:
                bmesh.update_edit_mesh(me)
                continue
            targets = self._targets(orig)
            if self.kind == KIND_RESTORE:
                restore.expand(bm, st["matches"], self.radius_scale, targets, self.profile_shift)
            elif self.kind == KIND_RINGS:
                if force or self.radius_scale != 1.0 or targets != orig or self.tidy:
                    chains = core.analyze(bm, self.fit_mode, whole=self.whole_shape)
                    if self.fit_mode == core.FIT_CIRCLE:
                        for chain in chains:
                            for ring in chain.rings:
                                checked += 1
                                bad += not core.ring_is_perfect(ring)
                    if [c.n for c in chains] == orig:
                        core.resample(bm, chains, targets, force=force, scale=self.radius_scale,
                                      tidy=self.tidy)
            else:
                if (force or self.even_spacing or self.profile_shift or self.radius_scale != 1.0
                        or targets != orig or self.tidy):
                    bands = curves.analyze(bm, self.fit_mode, cache=st["cache"], whole=self.whole_shape)
                    if [b.m for b in bands] == orig:
                        curves.resample(bm, bands, targets, even=self.even_spacing,
                                        shift=self.profile_shift, force=force,
                                        scale=self.radius_scale, tidy=self.tidy)
                        st["collapsed"] = (_collapse_records(bands, targets, self.profile_shift)
                                           if self.radius_scale == 0.0 else None)
            bmesh.update_edit_mesh(me)
        self._fixed = (bad, checked) if (self.kind == KIND_RINGS and self.fit_mode == core.FIT_CIRCLE) else None
        self._update_ui(context)

    def _shown_profile(self):
        if self._profile is None:
            return None
        return min(max(self._profile + self.profile_shift, curves.PROFILE_MIN), curves.PROFILE_MAX)

    def _update_ui(self, context):
        if context.area:
            cur = self._current()
            if self._uniform or self.use_absolute:
                info = f"Segments: {cur}"
            else:
                sign = "+" if self.delta >= 0 else ""
                info = f"Segments: {sign}{self.delta} on {self._shape_count} shapes"
            if self._typed:
                info += f"  [typing {self._typed}]"
            if self.kind == KIND_RINGS:
                if self._fixed is not None:
                    info += f"  |  Perfect circle: {self._fixed[0]} of {self._fixed[1]} fixed"
                if self.radius_scale != 1.0:
                    info += f"  |  Radius ×{self.radius_scale:.3f}"
                scope = "Whole" if self.whole_shape else "Selected"
                if self.tidy:
                    scope += "  |  Tidy"
                text = f"Roundabout  |  {info}  |  {scope}  |  Fit: {FIT_LABELS[self.fit_mode]}"
            elif self.kind == KIND_RESTORE:
                p = self._shown_profile()
                rad = ("Collapsed: LMB drag right" if self.radius_scale == 0.0
                       else f"Radius ×{self.radius_scale:.3f}")
                text = f"Roundabout (restore)  |  {info}  |  {rad}  |  Tension {p:.2f}"
            else:
                p = self._shown_profile()
                prof = f"Tension {p:.2f}" if p is not None else "Tension n/a"
                spacing = "Even spacing" if self.even_spacing else "Original spacing"
                rad = f"  |  Radius ×{self.radius_scale:.3f}" if self.radius_scale != 1.0 else ""
                if self.radius_scale == 0.0:
                    rad = "  |  Radius 0: removes the bevel"
                scope = "Whole" if self.whole_shape else "Selected"
                if self.tidy:
                    scope += "  |  Tidy"
                text = (f"Roundabout (curve)  |  {info}{rad}  |  {prof}  |  {spacing}  |  {scope}  |  "
                        f"Fit: {CURVE_FIT_LABELS[self.fit_mode]}")
            context.area.header_text_set(text)
            context.area.tag_redraw()
        if context.workspace:
            context.workspace.status_text_set({KIND_RINGS: STATUS_RINGS, KIND_RESTORE: STATUS_RESTORE}
                                              .get(self.kind, STATUS_CURVES))

    def _toggle_fix(self, context):
        """MMB click. Loops: make every loop a perfect circle (or undo that).
        Curves: spread the points evenly (or back to the original spacing)."""
        self._typed = ""
        if self.kind == KIND_RESTORE:
            return
        if self.kind == KIND_CURVES:
            self.even_spacing = not self.even_spacing
        elif self.fit_mode == core.FIT_CIRCLE:
            self.fit_mode = self._prev_fit if self._prev_fit != core.FIT_CIRCLE else core.FIT_AUTO
        else:
            self._prev_fit = self.fit_mode
            self.fit_mode = core.FIT_CIRCLE
        self._apply(context)

    # -- drags: LMB radius, RMB tension (curves) ----------------------------------

    def _begin_drag(self, context):
        pr = self._press
        pr["drag"] = True
        self._typed = ""
        if pr["button"] == 'LEFT':
            pr["base"] = self.radius_scale
        else:
            pr["base"] = self.profile_shift

    def _virtual_mouse(self, event):
        """Mouse position plus everything the cursor travelled across wraps."""
        off = self._press.get("off", (0, 0))
        return event.mouse_x + off[0], event.mouse_y + off[1]

    def _wrap_cursor(self, context, event):
        """Infinite drag: when the cursor reaches the edge of the 3D view it
        jumps to the opposite edge; the jump is added to the drag offset."""
        if not context.preferences.inputs.use_mouse_continuous:
            return
        region = self._region
        try:
            rx, ry, rw, rh = region.x, region.y, region.width, region.height
        except (ReferenceError, AttributeError):
            return
        margin = 4
        if rw <= 4 * margin or rh <= 4 * margin:
            return
        x, y = event.mouse_x, event.mouse_y
        nx, ny = x, y
        if x <= rx + margin:
            nx = rx + rw - margin - 2
        elif x >= rx + rw - margin:
            nx = rx + margin + 2
        if y <= ry + margin:
            ny = ry + rh - margin - 2
        elif y >= ry + rh - margin:
            ny = ry + margin + 2
        if (nx, ny) != (x, y):
            off = self._press.get("off", (0, 0))
            self._press["off"] = (off[0] + x - nx, off[1] + y - ny)
            context.window.cursor_warp(nx, ny)

    def _drag(self, context, event):
        pr = self._press
        ui = context.preferences.system.ui_scale
        mx, my = self._virtual_mouse(event)
        dx = mx - pr["x"]
        if pr["button"] == 'LEFT':
            if not self._has_radius:
                return  # nothing to scale: the drag does nothing
            # Right grows, left shrinks, wherever the drag starts.
            # RADIUS_DRAG px to the left always reaches 0 (the stop point);
            # from 0, RADIUS_DRAG px to the right gives the original size.
            step = dx / (RADIUS_DRAG * ui)
            if pr["base"] > 0.0:
                value = pr["base"] * (1.0 + step)
            else:
                value = step
            if event.ctrl:
                value = round(value / 0.05) * 0.05
            if self.kind in (KIND_CURVES, KIND_RESTORE):
                # Stop point: near zero the curve snaps to a single point.
                self.radius_scale = 0.0 if value < COLLAPSE_SNAP else min(value, 1000.0)
            else:
                self.radius_scale = min(max(value, 0.01), 1000.0)
        elif self.kind in (KIND_CURVES, KIND_RESTORE):
            value = pr["base"] + dx / (400.0 * ui)
            if event.ctrl and self._profile is not None:
                value = round((self._profile + value) / 0.05) * 0.05 - self._profile
            self.profile_shift = min(max(value, -1.0), 1.0)
        else:
            return  # RMB drag does nothing on loops
        self._apply(context)

    def _cancel(self, context):
        self._match_base = None
        self.delta = 0
        self.use_absolute = False
        self.fit_mode = core.FIT_AUTO
        self.radius_scale = self._initial_radius
        self.profile_shift = 0.0
        self.even_spacing = False
        self._apply(context)
        # Also drop the loops Space added.
        for me, snap in getattr(self, "_first_snaps", {}).values():
            bm = bmesh.from_edit_mesh(me)
            select_mode = set(bm.select_mode)
            bm.clear()
            bm.from_mesh(snap)
            bm.select_mode = select_mode
            bmesh.update_edit_mesh(me)
        self._cleanup(context)
        return {'CANCELLED'}

    def _finish(self, context):
        if self.kind == KIND_CURVES and self.radius_scale == 0.0:
            for st in self._states:
                if st.get("collapsed"):
                    restore.remember(st["mesh"], st["collapsed"])
        self._cleanup(context)
        return {'FINISHED'}

    def _cleanup(self, context):
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()
        if context.workspace:
            context.workspace.status_text_set(None)
        for st in self._states:
            snap = st.pop("snap", None)
            if snap is not None:
                bpy.data.meshes.remove(snap)
        for _, snap in getattr(self, "_first_snaps", {}).values():
            bpy.data.meshes.remove(snap)
        self._first_snaps = {}

    def modal(self, context, event):
        t, val = event.type, event.value

        if t in PASS_THROUGH or t.startswith('NDOF'):
            return {'PASS_THROUGH'}

        # MMB: a click fixes loops / evens out curves, a drag still orbits the
        # view, and Shift/Ctrl+MMB keep panning/zooming.
        if t == 'MIDDLEMOUSE':
            if val == 'PRESS':
                if event.shift or event.ctrl or event.alt or event.oskey:
                    return {'PASS_THROUGH'}
                self._mmb = (event.mouse_x, event.mouse_y)
            elif val == 'RELEASE' and self._mmb is not None:
                self._mmb = None
                self._toggle_fix(context)
            return {'RUNNING_MODAL'}
        if t == 'MOUSEMOVE' and self._mmb is not None:
            threshold = max(3, context.preferences.inputs.drag_threshold_mouse)
            if max(abs(event.mouse_x - self._mmb[0]), abs(event.mouse_y - self._mmb[1])) >= threshold:
                self._mmb = None
                bpy.ops.view3d.rotate('INVOKE_DEFAULT')
            return {'RUNNING_MODAL'}

        # LMB: a click applies, a drag changes the radius.
        # RMB: a click cancels, a drag changes the tension of open curves.
        if t in {'LEFTMOUSE', 'RIGHTMOUSE'}:
            button = 'LEFT' if t == 'LEFTMOUSE' else 'RIGHT'
            if val in {'PRESS', 'DOUBLE_CLICK'}:
                if self._press is None:
                    self._press = {"button": button, "x": event.mouse_x, "y": event.mouse_y, "drag": False}
            elif val == 'RELEASE' and self._press is not None and self._press["button"] == button:
                dragged = self._press["drag"]
                self._press = None
                if not dragged:
                    if button == 'RIGHT':
                        return self._cancel(context)
                    return self._finish(context)
                self._update_ui(context)
            return {'RUNNING_MODAL'}

        if t == 'MOUSEMOVE':
            self._mouse = (event.mouse_region_x, event.mouse_region_y)
            pr = self._press
            if pr is not None:
                if not pr["drag"]:
                    threshold = max(3, context.preferences.inputs.drag_threshold_mouse)
                    if max(abs(event.mouse_x - pr["x"]), abs(event.mouse_y - pr["y"])) >= threshold:
                        self._begin_drag(context)
                if pr["drag"]:
                    self._drag(context, event)
                    self._wrap_cursor(context, event)
                    return {'RUNNING_MODAL'}
            if context.area:
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if val != 'PRESS':
            return {'RUNNING_MODAL'}

        # Toggle off / confirm.
        if (t == 'T' and event.alt) or t in CONFIRM:
            return self._finish(context)

        if t in CANCEL:
            return self._cancel(context)

        if t in INCREASE or t in DECREASE:
            self._step(1 if t in INCREASE else -1, snap=event.ctrl)
            self._apply(context)
            return {'RUNNING_MODAL'}

        if t in DIGITS:
            self._match_base = None
            self._typed = (self._typed + DIGITS[t])[-4:]
            value = int(self._typed)
            if value >= self._min_segments:
                self.use_absolute = True
                self.segments = min(value, self._max_segments)
                self._apply(context)
            else:
                self._update_ui(context)
            return {'RUNNING_MODAL'}

        if t == 'BACK_SPACE':
            self._match_base = None
            self._typed = self._typed[:-1]
            if self._typed and int(self._typed) >= self._min_segments:
                self.segments = min(int(self._typed), self._max_segments)
                self.use_absolute = True
            elif not self._typed and self.use_absolute:
                # Back to relative mode at the count currently shown.
                self.use_absolute = False
                self._set_primary(self.segments)
            self._apply(context)
            return {'RUNNING_MODAL'}

        if t in {'E', 'D'}:
            # Match every shape to the highest (E) or lowest (D) count. The
            # counts from before the first E / D are remembered, so E and D
            # switch between the two (until the counts are changed otherwise).
            if self._match_base is None:
                self._match_base = [n for st in self._states for n in self._targets(self._orig(st))]
            counts = self._match_base
            if counts:
                self._typed = ""
                self.use_absolute = True
                value = max(counts) if t == 'E' else min(counts)
                self.segments = max(self._min_segments, min(self._max_segments, value))
                self._apply(context)
            return {'RUNNING_MODAL'}

        if t == 'SPACE' and self.kind == KIND_RINGS:
            self._match_base = None
            self._add_similar(context)
            return {'RUNNING_MODAL'}

        if t == 'R' and self.kind != KIND_RESTORE:
            self.tidy = not self.tidy
            self._typed = ""
            self._apply(context)
            return {'RUNNING_MODAL'}

        if t == 'W' and self.kind != KIND_RESTORE:
            key = "rings" if self.kind == KIND_RINGS else "curves_w"
            if any(st[key][not self.whole_shape] for st in self._states):
                self.whole_shape = not self.whole_shape
                self._match_base = None
                # Remembered: the next Alt+T starts in this mode, until W is
                # pressed again (also stored in the add-on preferences).
                _prefs(context).default_whole_shape = self.whole_shape
                self._update_stats()
                self._apply(context)
            return {'RUNNING_MODAL'}

        if t == 'F' and self.kind != KIND_RESTORE:
            i = FIT_CYCLE.index(self.fit_mode)
            self.fit_mode = FIT_CYCLE[(i + 1) % len(FIT_CYCLE)]
            self._apply(context)
            return {'RUNNING_MODAL'}

        return {'RUNNING_MODAL'}


def _draw_hud(op):
    try:
        mx, my = op._mouse
    except (AttributeError, ReferenceError):
        return
    ui = bpy.context.preferences.system.ui_scale
    font = 0
    x = mx + int(24 * ui)
    y = my + int(16 * ui)

    blf.enable(font, blf.SHADOW)
    blf.shadow(font, 3, 0.0, 0.0, 0.0, 0.8)
    blf.shadow_offset(font, 1, -1)

    cur = op._current()
    big = str(cur) if (op._uniform or op.use_absolute) else f"{'+' if op.delta >= 0 else ''}{op.delta}"
    _blf_size(font, 26 * ui)
    blf.color(font, 1.0, 0.82, 0.35, 1.0)
    blf.position(font, x, y, 0)
    blf.draw(font, big)

    w, _ = blf.dimensions(font, big)
    _blf_size(font, 12 * ui)
    rings = op.kind == KIND_RINGS
    restoring = op.kind == KIND_RESTORE
    TEXT, KEY = (0.9, 0.9, 0.9, 0.9), (0.6, 0.6, 0.6, 0.85)
    VALUE, WARN, GOOD = (1.0, 0.82, 0.35, 0.95), (1.0, 0.45, 0.35, 0.95), (0.55, 0.9, 0.55, 0.95)

    def parts(px, py, items):
        """Draw (text, color) pieces side by side; key hints are dimmer."""
        for text, col in items:
            blf.color(font, *col)
            blf.position(font, px, py, 0)
            blf.draw(font, text)
            px += blf.dimensions(font, text)[0]

    label = "segments"
    parts(x + w + 8 * ui, y + 10 * ui,
          [(label + (f"  ({op._typed}_)" if op._typed else ""), TEXT), ("   Wheel", KEY)])
    if restoring:
        line = [("restore bevel", TEXT)]
    elif rings:
        line = [(f"fit {FIT_LABELS[op.fit_mode].lower()}", TEXT), ("  F", KEY), ("   |   ", TEXT),
                ("whole" if op.whole_shape else "selected", TEXT), ("  W", KEY)]
    else:
        line = [(f"fit {CURVE_FIT_LABELS[op.fit_mode].lower()}", TEXT), ("  F", KEY), ("   |   ", TEXT),
                ("even spacing" if op.even_spacing else "original spacing", TEXT), ("  MMB", KEY),
                ("   |   ", TEXT), ("whole" if op.whole_shape else "selected", TEXT), ("  W", KEY)]
    parts(x + w + 8 * ui, y - 4 * ui, line)

    rows = []
    if not restoring:
        differ = not op._uniform and not op.use_absolute
        rows.append([("match counts" if differ else "match max / min", VALUE if differ else TEXT),
                     ("   E max · D min" if differ else "   E / D", KEY)])
    press = op._press or {}
    dragging = press.get("drag")
    if not op._has_radius:
        rows.append([("radius n/a", TEXT)])
    elif op.radius_scale == 0.0 and not rings:
        if restoring:
            rows.append([("collapsed", WARN), ("   LMB drag right", KEY)])
        else:
            rows.append([("radius 0: removes the bevel", WARN), ("   LMB drag right", KEY)])
    else:
        rows.append([(f"radius ×{op.radius_scale:.3f}", VALUE), ("   LMB drag", KEY)])
    if not restoring:
        if op.tidy:
            rows.append([("tidy: on", GOOD), ("   R", KEY)])
        else:
            rows.append([("tidy", TEXT), ("   R", KEY)])
    if rings:
        if op._fixed is not None:
            rows.append([(f"perfect circle: {op._fixed[0]} of {op._fixed[1]} fixed", GOOD), ("   MMB undo", KEY)])
        else:
            rows.append([("perfect circle", TEXT), ("   MMB", KEY)])
        if op._similar_added is None:
            rows.append([("select similar", TEXT), ("   Space", KEY)])
        else:
            rows.append([(f"select similar: +{op._similar_added}", GOOD), ("   Space", KEY)])
    else:
        p = op._shown_profile()
        if p is None:
            rows.append([("tension n/a", TEXT)])
        else:
            hint = "   0.25 flat · 0.5 round · 1 sharp" if dragging and press.get("button") == 'RIGHT' else ""
            rows.append([(f"tension {p:.2f}", VALUE), ("   RMB drag" + hint, KEY)])
    yy = y - 22 * ui
    for row in rows:
        parts(x, yy, row)
        yy -= 16 * ui
    blf.disable(font, blf.SHADOW)


def _collapse_records(bands, targets, shift):
    out = []
    for band, M in zip(bands, targets):
        if band.collapsed is None:
            continue
        coords, radii = band.collapsed
        if not any(r > 0.0 for r in radii):
            continue
        p = band.profile if band.profile is not None else 0.5
        p = min(max(p + shift, curves.PROFILE_MIN), curves.PROFILE_MAX)
        out.append(restore.Record(coords, radii, band.closed, band.sel, max(1, int(M)), p))
    return out


def menu_func(self, context):
    self.layout.separator()
    self.layout.operator(MESH_OT_roundabout.bl_idname, text="Roundabout")


classes = (MESH_OT_roundabout,)
