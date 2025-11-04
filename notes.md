# High-level overview

Render a final on-call schedule by combining a base rotating schedule with overrides.

Algorithm choice and justification (high level):

- Base rotation generation: We deterministically produce shift boundaries from the schedule's `handover_start_at` every `handover_interval_days`. This ensures exact alignment with handover times, no cumulative floating errors, and O(K) complexity where K is the number of shifts that intersect the requested window.
- Override application via interval splitting (sweep-style): We represent the schedule as a chronologically ordered list of disjoint intervals [start, end, user]. For each override, we split any overlapping base intervals into up to three parts: left (base), middle (override), right (base). This local, immutable transformation guarantees no overlaps and preserves ordering, while supporting arbitrary overlap patterns and any override user (not limited to base users). Complexity is O(N * M) in worst case where N is base intervals and M overrides; in practice N is small (windowed by `--from/--until`).
- Truncation and coalescing: Finally, we clamp to the requested time window and merge adjacent intervals with the same user to keep output compact and intuitive.

Why not priority queues or segment trees? Those structures help for high-frequency, dynamic
updates. Here the input is small and static per invocation; correctness and readability are
more important than asymptotic micro-optimizations. The split-and-merge approach remains easy
to reason about and test, and avoids pitfalls like off-by-one at handover boundaries.

Override precedence: Later overrides in the input array take precedence over earlier ones
on overlapping periods. This is a predictable, stable rule often used in scheduling tools.
