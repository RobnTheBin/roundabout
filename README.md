# Roundabout (Blender add-on)

[![Tests](https://github.com/RobnTheBin/roundabout/actions/workflows/tests.yml/badge.svg)](https://github.com/RobnTheBin/roundabout/actions/workflows/tests.yml)
[![Latest release](https://img.shields.io/github/v/release/RobnTheBin/roundabout)](https://github.com/RobnTheBin/roundabout/releases/latest)
[![License: GPL v3+](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)

Roundabout changes the number of segments on cylindrical shapes while you work, using the mouse wheel. The rest of the shape stays the same: bevels, insets, inner walls and caps all keep their form. It also resamples open curves such as bevel profiles, so you can change a bevel's segment count and shape after the fact.

## Install

**[Download the latest `roundabout-x.y.z.zip`](https://github.com/RobnTheBin/roundabout/releases/latest)** from the Releases page. Don't unzip it.

Blender 4.2 and newer (extension):
1. Go to **Edit → Preferences → Get Extensions → ⌄ (top right) → Install from Disk…**
2. Pick the downloaded zip.

Blender 3.6 to 4.1 (legacy add-on): download the source, zip the `roundabout` folder, then install that zip from **Preferences → Add-ons → Install…**

## Use

1. In **Edit Mode**, select one or more ring loops (Alt+click a loop around the cylinder). Edge, vertex and face select modes all work. In face mode you can select the side faces.
2. Press **Alt+T**. Resample mode is now on, and the segment count shows next to the cursor. The HUD lists every value you can change with its shortcut in grey next to it (Wheel, F, W, LMB drag, RMB drag, MMB click). LMB and RMB drags are infinite: when the cursor reaches the edge of the 3D view it wraps around to the other side and the value keeps changing, like Blender's own transform tools. This follows **Preferences → Input → Mouse → Continuous Grab**.
3. Use the mouse wheel to add or remove segments. The mesh updates live. By default **only the selected loops** change. Press **W** to resample the whole shape instead.
4. Drag with the left mouse button to change the radius of the loops.
5. Press **Alt+T** again, or click the left mouse button, to apply. Enter also applies.

Select an **open edge path** instead of a loop, for example one profile path across a bevel, to resample that curve. See [Open curves and bevels](#open-curves-and-bevels).

| Key | Action |
| --- | --- |
| Wheel up / down (or `+` `-`, arrow keys) | ±1 segment |
| Ctrl+Wheel | Jump to the next or previous multiple of 4 |
| **LMB drag** | Change the radius of loops and curves (Ctrl snaps to steps of 0.05) |
| **RMB drag** | Curves: change the tension, tighter or flatter (Ctrl snaps) |
| **MMB click** | Loops: snap the loops being resampled to perfect, evenly spaced circles. Curves: space the points evenly. Click again to turn it off |
| `W` | Switch between **selected loops only** and **whole shape** (loops only) |
| `0`–`9`, Backspace | Type an exact segment count |
| `F` | Cycle the fit mode: Auto → Circle → Keep Profile |
| `Space` | Loops: also take every other loop with the same vertex count, in all objects in edit mode (only selects; counts don't change) |
| `E` / `D` | Give every shape the highest (E) or lowest (D) of the current counts |
| LMB click / Alt+T / Enter | Apply (turn resample mode off) |
| Esc / RMB click | Cancel and restore the original mesh |
| MMB drag, Shift/Ctrl+MMB | Orbit, pan and zoom the viewport as usual |

After you apply, the **Adjust Last Operation** panel (F9) lets you change the count, switch to an exact count, change the fit mode, and set **Radius** and **Whole Shape** (loops) or **Radius**, **Tension** and **Even Spacing** (curves). The tool is also in **Edge menu → Roundabout** and in the right-click context menu. You can change the shortcut in the add-on's preferences. The mode you switch to with W is remembered: Alt+T keeps starting in it until you press W again. It's also shown, and can be set, in the add-on preferences.

### Loops with the same count (Space)

While resampling loops, press **Space** to add every other closed loop that has the same vertex count, in all objects currently in edit mode. Space only selects: no count changes. If you had already changed the count, the added loops get the same change (+2 stays +2 on each of them). The wheel then changes them all together. If the loops you selected are round, only round loops are added, so an oval or square loop with the same count stays as it is. Esc drops the added loops again. The redo panel (F9) has the same option as **Same-Count Loops**. On very dense meshes the search takes a moment (about 1.7 s for 68k vertices). Open curves don't have this: there is no reliable way to tell which other edge paths are "the same" bevel profile.

### Selected loops vs. whole shape (W)

- **Selected loops** (default): only the loops you selected get the new count. Each one is joined to the unselected loops next to it with quads, plus exactly as many triangles as the counts differ by. For example, going 16 → 24 adds 8 triangles to each neighbouring band. If you select loops that sit next to each other, the band between them stays all quads. Run the tool again on a loop like that and its mixed quad/triangle bands are rebuilt from scratch. If the new count matches the neighbouring loop, or divides evenly into it, the new vertices line up with that neighbour. Going back to the original count, in one step or several, gives clean quads with straight edges again. An n-gon or fan cap on a selected loop is rebuilt to match.
- **Whole shape**: every loop connected to the selection through quad strips (side, bevels, insets, inner wall) is resampled together, so the result stays all quads. Use this to change the resolution of a whole beveled cylinder from one selected loop.

### Fixing loops that aren't round (MMB)

A click of the middle mouse button turns **fix loops** on. Each loop being resampled becomes a perfect circle with evenly spaced vertices. In selected-loops mode that's just your selection. In whole-shape mode (W) it's every loop of the shape, including bevel rings, cap rings and inner walls. Use it on loops that were dented by booleans, knife cuts, sculpting or hand tweaks. It works at the current segment count, so you can fix a shape without resampling it, and it stays on while you change the count with the wheel. The HUD shows how many loops needed fixing.

The fix keeps the shape as close to the original as it can:
- For each ring it finds the plane and circle closest to the existing vertices (least squares), and the rotation that moves the vertices least.
- Rings that are already perfect don't move at all.
- At the same segment count, only vertex positions change. Topology, vertex order, UVs and all other data stay as they are.
- Each ring keeps its own center, radius and tilt, so tapers and bevel profiles keep their shape.

Click MMB again to turn the fix off, or press Esc to cancel everything. Only a click turns the fix on or off; dragging with MMB orbits the view as usual. The same option is in the redo panel: set **Fit → Circle** with a change of 0.

### Radius (LMB drag)

Drag with the left mouse button to scale the loops being resampled. Drag **right** to make them bigger and **left** to make them smaller, wherever you start the drag. 300 px is a 100 % change. Each ring is scaled in its own plane about its own fitted center, so tilted rings and tapers keep their shape. In selected-loops mode only the selected loops change and the faces next to them stretch to follow. In whole-shape mode the whole cross-section scales, bevels included. Hold Ctrl to snap the factor to steps of 0.05. A plain click without dragging still applies.

## Open curves and bevels

When the selection has no closed loop, the tool works on **open edge paths** instead. Select a path with Alt+click or Shift+click, for example from the side wall across a bevel to the cap, then press Alt+T.

The selected path is one column of a band of quads: the bevel all the way around a cylinder, or the bevel along a cube edge. Every column of that band is resampled together, so the result stays all quads and you only need to select **one** path per bevel. Several paths on different bevels work at the same time, each relative to its own count. Edge and vertex select mode both work.

- **Wheel**: change the number of segments along the curve. One segment is a plain chamfer. The ends of the curve never move.
- **LMB drag**: change the **radius**. A bevel scales about its corner, so its ends slide along the side wall and the cap and the bevel gets wider or narrower. A plain arc, such as part of a ring, scales about its circle center, and its ends move with it. The HUD factor applies to the curve you selected; every other column of its band (for example the loops of a smaller bevel that wraps around the end of the big one) grows or shrinks by the same distance, so they stay parallel instead of tapering. Hold Ctrl to snap to steps of 0.05.
  - **Stop point at 0:** dragging left stops at radius 0 (300 px to the left always gets there, from any size; it snaps just before, and dragging further stays at 0). The HUD turns red and says the curve will merge. You are still in the tool: drag right to grow it back from 0 (300 px is the original size). Confirm, and every column of the band is merged into a single vertex: at its corner for a bevel, so **the bevel is removed** and the faces meet in a sharp edge again; at the circle center for a plain arc. The new sharp edge is selected. Loops never collapse; their radius stops at 0.01.
  - **Bringing it back:** as long as the mesh stays in edit mode, the collapse is remembered. With the collapsed edge selected (it is right after collapsing), press Alt+T: the HUD says *restore collapsed bevel* and the radius starts at 0. Drag LMB to the right to rebuild the bevel; 300 px gives the original width. The wheel sets the bevel's segment count (it starts at the count it had) and RMB drag its tension. The bevel is rebuilt with Blender's Bevel, so UVs and attributes are interpolated like a normal bevel. Leaving edit mode, or changing the collapsed vertices, forgets it; Alt+T on that edge then works on it as a normal loop or curve again. While a collapse can still be restored, Alt+T on that edge always restores it; to resample the sharp edge as a loop instead, leave edit mode once.
- **RMB drag**: change the **tension**. Drag right for a tighter curve (toward a sharp corner) and left for a flatter one (toward a straight line). The ends stay where they are. The HUD shows the value on the same scale as the Profile setting of Blender's Bevel: 0.25 is straight, 0.5 is round, 1 is a sharp corner. Hold Ctrl to snap to steps of 0.05. A chamfer dragged to 0.5 becomes a round fillet.
- **RMB click** (without dragging) cancels, as before.
- **MMB click**: space the points evenly along the curve (equal edge lengths). Click again to go back to the original spacing.
- **F**: fit mode. **Auto** uses the exact profile when the curve is a clean bevel profile and a smooth spline otherwise. **Circle** (*Exact Profile* for curves) always snaps to the best-fit profile. **Keep Profile** (*Spline*) always follows a spline through the original points.

How the curve is fitted: the tool finds where the geometry continues past both ends of the curve (the side wall and the cap for a bevel) and the corner where those two directions meet. A round bevel is an exact arc in that corner, and other profiles are the same superellipse family Blender's Bevel uses. A curve without such a corner, such as half of a cylinder's top loop, is fitted with an exact circular arc instead, and whichever of the two fits is closer wins. New points land exactly on the fitted curve, so going from 3 to 12 bevel segments gives a properly round bevel.

Tension works on any curve that bends. With a corner it reshapes the profile in that corner. Without one, the curve is cut into pieces that each turn at most about 100° (a half circle becomes two quarters), and each piece is tightened toward its own corner, so a half ring tightens toward a box shape. Lower tension flattens the whole curve toward the straight line between its ends (a half ring turns into a flat D side at 0.25). By default new points keep the original spacing pattern: a bevel that is denser near one end stays denser there.

Faces past the ends of an open band (the end faces of a beveled cube edge, for example) are re-stitched to the new points, so the mesh stays closed. An open path along a ring, such as part of a cylinder's top loop, also works: that sector of the cylinder gets more or fewer segments and the caps are re-stitched.

## What it handles

In **whole-shape** mode you only need to select **one** ring. The tool follows clean quad strips from it and collects every connected ring, so the whole shape keeps all-quad topology. The walk goes through:

- side walls and extra loop cuts
- **bevels** of any segment count, at the cap edges or anywhere else
- inset cap rings (annuli)
- the inner wall of a pipe or tube with thickness. That shape is a closed chain of rings, like a torus.
- UV spheres and cones (rings toward a pole)

Where a chain ends:

| End type | Result |
| --- | --- |
| N-gon cap | Rebuilt as one n-gon |
| Triangle fan (cone tip, sphere pole, "Triangle Fan" caps) | Rebuilt as a fan around the same center vertex |
| Open boundary | Left open |
| Anything else, e.g. a boss merged into a plate | The touching faces are re-stitched to the new ring vertices, so the surrounding mesh stays connected |

New vertices sit on the **true fitted circle** of each ring, not on the old flat facets. Going 8 → 64 gives a properly round result. Each ring keeps its own center, radius and plane, so tapers, cones and bevel profiles keep their shape. The first vertex of every ring stays where it was, so seams and alignment don't drift.

**Fit modes**
- **Auto** (default): uses Circle when every ring is round to within 1 %, otherwise Keep Profile. At the original count, Auto returns the exact original mesh.
- **Circle** (what an MMB click turns on): always fits a perfect circle, at any segment count, including the original one.
- **Keep Profile**: draws a smooth periodic spline through the original vertices. Use it for elliptical, scaled or irregular sections.

**Data that is kept:** materials, smooth/flat shading, UVs and color attributes (interpolated from the original faces), vertex groups (interpolated), crease and bevel weights, seams and sharp edges. A seam or sharp mark on a rail (the edges running along the cylinder) moves to the nearest new rail and is never duplicated. Marks on ring edges carry over to the whole new ring. Selection is kept, so you can run the tool again right away. Several objects in multi-object edit mode work too. Each shape changes relative to its own segment count.

## Limitations

- **Meshes with shape keys are skipped.** Changing the vertex count would break them.
- Custom split normals on the resampled faces are interpolated, which can look wrong. A Weighted Normal modifier works better.
- If the selection holds both closed loops and open paths, only the loops are resampled.
- Tension needs a curve that bends. On a straight path the HUD shows *tension: n/a*; resampling, radius and even spacing still work. Radius needs a corner or a circle fit, so it does nothing on a straight path without a corner. A plain corner edge of a box (both ends only touch ordinary quads) is not a chamfer: the HUD shows *radius: n/a* and dragging does nothing.
- Rings must be closed loops. A strip whose ring has a different vertex count, triangles, or poles is not walked through: the chain stops there and that side is treated as "anything else" (re-stitched).
- In selected-loops mode, the bands next to the selection get triangles. On a smooth-shaded cylinder they shade cleanly, but if you need all quads, use whole-shape mode (W).
- Removing many segments next to irregular merged geometry can collapse some faces that touch the ring. They are merged away rather than left as degenerate faces.
- In resample mode the wheel is used by the tool, an MMB *click* turns loop fixing or even spacing on or off, and an LMB drag changes the radius and an RMB drag the tension. A plain RMB click still cancels. MMB drags, and Ctrl/Shift+MMB, still navigate as usual.
- Other add-ons may also use **Alt+T** (some hard-surface tool packs bind keys in Edit Mode). If the shortcut doesn't fire, rebind it in this add-on's preferences.

## Development

```
roundabout/
  __init__.py              registration, Alt+T keymap, preferences
  operators.py             modal operator, HUD, redo panel
  core.py                  ring detection, chain walk, fitting, rebuild (pure BMesh)
  curves.py                open paths: band walk, bevel profile fit, rebuild (pure BMesh)
  restore.py               collapsed bevels remembered for the edit session, re-bevel
  blender_manifest.toml    extension manifest (4.2+)
tests/
  run_tests.py             headless geometry tests
  gui_modal_test.py        drives the real Alt+T / wheel / Esc flow in a live window
```

Run the tests:

```bash
blender -b --factory-startup --python tests/run_tests.py
```

```bash
blender --factory-startup --enable-event-simulate --python tests/gui_modal_test.py
```

Build the extension zip:

```bash
blender --factory-startup --command extension build --source-dir roundabout --output-dir dist
```

**How the interactive mode works:** pressing Alt+T stores a snapshot of the edit mesh. Each wheel step restores the snapshot and resamples it again from scratch. Errors never pile up, the original count gives back the exact original mesh, and Esc is a clean undo. On a mesh of about 90k vertices one wheel step takes around 16 ms.

**Releasing:** set `version` in `roundabout/blender_manifest.toml` (and `bl_info` in `__init__.py`), add a section to `CHANGELOG.md`, then push a tag `vX.Y.Z`. The Release workflow builds and validates the zip with Blender and publishes it on the Releases page with the changelog section as notes.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE). © 2026 Robin Mariančík.
