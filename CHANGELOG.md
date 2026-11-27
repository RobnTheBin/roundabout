# Changelog

## 1.1.1

- E and D switch freely: they match against the counts from before the first E / D, so after D you can press E to get the highest count back (until you change the count another way).
- The strip between two selected curves side by side is rebuilt with them, whatever faces it holds. With the same count, point 0 joins point 0, 1 joins 1 and so on, the end points too; a vertex where other geometry meets the ends gets a small face of its own beside the strip. With different counts the even pattern is used. With tidy (R) this also happens without changing the count. Before, it was patched face by face and stayed messy.
- Selected curves side by side with different counts, where one lines up with only part of the other, are both found as curves of their own, so E / D and exact counts change both (before, only one was).
- The hint next to the cursor always lists E / D (match max / min), not only while the selected counts differ.
- Two selected curves with the same count, joined by a zig-zag, are now lined up and rebuilt as quads even when one end of the strip runs through an extra vertex (where other geometry meets them). The end points are joined directly, and that vertex gets a small face of its own.

## 1.1.0

- Shorter labels on screen: perfect circle, select similar, tidy, selected / whole, match max / min.
- New: **R** tidies the connections while resampling, on loops and curves. The strips joining the changed loops or curves to their neighbours are rebuilt with an even pattern: quads where the counts match (the loop turns to line up), and where they differ, the triangles are spread evenly (mirrored about the middle on curves). Tidy also straightens a twisted quad strip between loops, so each edge across it takes the shortest way, and it works at the current count too (press R without changing the count). Also in the Adjust Last Operation panel.
- Open curves follow the W switch too. In selected mode only the picked curves change, not every parallel curve connected through quads (such as the arcs down the whole side of a shape); triangles join them to their neighbours, and going back to the neighbour's count gives clean quads again. A bevel or chamfer profile still changes all the way along the bevel.
- A quad beside a curve that gained points is split into triangles instead of becoming one long n-gon.
- Selected loops joined by a zig-zag of triangles (loops that don't line up) are lined up when they get the same count, and the strip between them becomes clean quads. With different counts it is rebuilt as one clean zig-zag, so lowering the count and raising it again no longer leaves a mess.

## 1.0.1

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
