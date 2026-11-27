# SPDX-FileCopyrightText: 2026 Robin Mariančík
# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {
    "name": "Roundabout",
    "author": "Robin Mariančík",
    "version": (1, 0, 1),
    "blender": (3, 6, 0),
    "location": "Edit Mesh > select ring loops or a bevel profile > Alt+T, then mouse wheel / drag",
    "description": "Resample loops, arcs and bevels interactively: segments, radius, tension",
    "category": "Mesh",
}

import bpy

from . import operators, restore

_keymaps = []


class RoundaboutPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    default_whole_shape: bpy.props.BoolProperty(
        name="Whole Shape mode",
        description="When on, Alt+T resamples every loop connected to the selection (all-quad result). "
                    "When off, only the selected loops change. Pressing W while resampling switches it, "
                    "and the choice is remembered until W is pressed again",
        default=False,
    )

    def draw(self, context):
        import rna_keymap_ui

        layout = self.layout
        layout.prop(self, "default_whole_shape")
        col = layout.column()
        col.label(text="Shortcut (Edit Mesh):")
        kc = context.window_manager.keyconfigs.user
        km = kc.keymaps.get("Mesh")
        if km is None:
            col.label(text="Mesh keymap not found", icon='ERROR')
            return
        found = False
        for kmi in km.keymap_items:
            if kmi.idname == operators.MESH_OT_roundabout.bl_idname:
                rna_keymap_ui.draw_kmi([], kc, km, kmi, col, 0)
                found = True
        if not found:
            col.label(text="Shortcut was removed. Re-enable the add-on to restore Alt+T.", icon='INFO')


def _register_keymap():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc is None:  # background mode
        return
    km = kc.keymaps.new(name="Mesh", space_type='EMPTY')
    kmi = km.keymap_items.new(operators.MESH_OT_roundabout.bl_idname, 'T', 'PRESS', alt=True)
    _keymaps.append((km, kmi))


def _unregister_keymap():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()


def register():
    for cls in operators.classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(RoundaboutPreferences)
    bpy.types.VIEW3D_MT_edit_mesh_edges.append(operators.menu_func)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(operators.menu_func)
    _register_keymap()
    if restore.on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(restore.on_depsgraph_update)


def unregister():
    if restore.on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(restore.on_depsgraph_update)
    restore.clear()
    _unregister_keymap()
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(operators.menu_func)
    bpy.types.VIEW3D_MT_edit_mesh_edges.remove(operators.menu_func)
    bpy.utils.unregister_class(RoundaboutPreferences)
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
