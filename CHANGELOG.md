# Changelog

## Unreleased

- Radius works on a chamfer looped around a shape with sharp corners; the corner (miter) edges no longer turn it off for the whole loop, and they scale in step with the rest.
- Radius works on hand-made chamfers whose faces are slightly twisted, and on chamfer edges that only touch quads when the rest of the loop is a real chamfer.
- Parallel open curves with the same segment count stay one band even when the strip between them is a zig-zag of triangles (e.g. left by an earlier resample), and that strip is rebuilt as clean quads.

## 1.0.0

First public release.

- Resample ring loops with the mouse wheel (Alt+T in Edit Mode), live, on one or many objects.
- Selected loops or the whole shape (W): bevels, insets, inner walls and caps keep their form and topology.
- Radius (LMB drag), fix loops to perfect circles (MMB click), exact counts by typing, fit modes (F).
- Same-count loops (Space), match the highest or lowest count (E / D).
- Open curves and bevel profiles: segments, radius, tension (RMB drag) and even spacing.
- Collapse a bevel by dragging its radius to 0, and bring it back while still in Edit Mode.
- UVs, attributes, vertex groups, seams, sharp edges, creases and bevel weights are kept.
