# Roundabout

[![Tests](https://github.com/RobnTheBin/roundabout/actions/workflows/tests.yml/badge.svg)](https://github.com/RobnTheBin/roundabout/actions/workflows/tests.yml)
[![Latest release](https://img.shields.io/github/v/release/RobnTheBin/roundabout)](https://github.com/RobnTheBin/roundabout/releases/latest)
[![License: GPL v3+](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)

A Blender add-on for changing the number of segments on cylinders, loops and bevels with the mouse wheel, without rebuilding them. Bevels, insets, inner walls and caps keep their shape, and UVs, seams and other mesh data are kept.

## Install

1. **[Download the latest `roundabout-x.y.z.zip`](https://github.com/RobnTheBin/roundabout/releases/latest)** (don't unzip it).
2. In Blender 4.2 or newer: **Edit → Preferences → Get Extensions → ⌄ (top right) → Install from Disk…** and pick the zip.

## Quick start

1. In Edit Mode, select a loop around a cylinder (Alt+click), or one edge path across a bevel.
2. Press **Alt+T**.
3. Scroll the mouse wheel to add or remove segments.
4. Click to apply, or press Esc to cancel.

## Shortcuts

| Key | What it does |
| --- | --- |
| **Wheel** (or `+` / `-`) | More or fewer segments |
| **Ctrl+Wheel** | Jump to the next multiple of 4 |
| `0`–`9` | Type an exact segment count |
| **LMB drag** | Radius: drag right to grow, left to shrink |
| **RMB drag** | Bevels and curves: tension, tighter or flatter |
| **MMB click** | Loops: perfect circle. Curves: even spacing |
| `W` | Change only the selected loops or curves, or the whole shape (remembered) |
| `R` | Tidy: quads where counts match, evenly spread triangles where they don't |
| `Space` | Select similar: every loop with the same count |
| `E` / `D` | Set all selected shapes to the highest / lowest count |
| `F` | Fit mode: Auto, Circle, Keep Profile |
| **Click**, Enter or Alt+T | Apply |
| Esc or **RMB click** | Cancel |

You can still orbit, pan and zoom the view with the middle mouse button while the tool is on. After applying, the **Adjust Last Operation** panel (F9) lets you fine-tune the result.

## Tips

- **Whole shape (W)** changes every connected loop of a cylinder at once, bevels included, and keeps all quads. **Selected loops** changes only what you picked and joins it to its neighbours with a few triangles. Open curves work the same way: in selected mode only the curves you picked change.
- **Bevels:** select one edge path across a bevel to change its segments, width (LMB drag) and roundness (RMB drag). The whole bevel updates together, in either mode.
- **Remove a bevel:** drag left until the radius hits 0 and apply. While you stay in Edit Mode you can bring it back: select only that edge, press Alt+T and drag right.
- The shortcut can be changed in the add-on's preferences. The tool is also in the **Edge** menu and the right-click menu.

## Limitations

- Meshes with shape keys are skipped.
- If Alt+T is taken by another add-on, set a different shortcut in the preferences.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE). © 2026 Robin Mariančík.
