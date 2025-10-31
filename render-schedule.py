#!/usr/bin/env python3
"""
Render a final on-call schedule by combining a base rotating schedule with overrides.

Algorithm choice and justification (high level):
- Base rotation generation: We deterministically produce shift boundaries from the
  schedule's `handover_start_at` every `handover_interval_days`. This ensures exact
  alignment with handover times, no cumulative floating errors, and O(K) complexity
  where K is the number of shifts that intersect the requested window.
- Override application via interval splitting (sweep-style): We represent the schedule
  as a chronologically ordered list of disjoint intervals [start, end, user]. For each
  override, we split any overlapping base intervals into up to three parts: left (base),
  middle (override), right (base). This local, immutable transformation guarantees no
  overlaps and preserves ordering, while supporting arbitrary overlap patterns and any
  override user (not limited to base users). Complexity is O(N * M) in worst case where N
  is base intervals and M overrides; in practice N is small (windowed by `--from/--until`).
- Truncation and coalescing: Finally, we clamp to the requested time window and merge
  adjacent intervals with the same user to keep output compact and intuitive.

Why not priority queues or segment trees? Those structures help for high-frequency, dynamic
updates. Here the input is small and static per invocation; correctness and readability are
more important than asymptotic micro-optimizations. The split-and-merge approach remains easy
to reason about and test, and avoids pitfalls like off-by-one at handover boundaries.

Override precedence: Later overrides in the input array take precedence over earlier ones
on overlapping periods. This is a predictable, stable rule often used in scheduling tools.
"""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Tuple


ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def parse_iso8601_z(s: str) -> datetime:
    if s.endswith("Z"):
        # strict 'Z' handling; ensure UTC tz-aware
        return datetime.strptime(s, ISO_FMT).replace(tzinfo=timezone.utc)
    # Fallback: try fromisoformat to support offsets if provided
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # Assume UTC if naive (inputs are expected in Z)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO_FMT)


@dataclass
class Interval:
    start: datetime
    end: datetime
    user: str

    def overlaps(self, other_start: datetime, other_end: datetime) -> bool:
        return self.start < other_end and other_start < self.end

    def clipped(self, clip_start: datetime, clip_end: datetime):
        s = max(self.start, clip_start)
        e = min(self.end, clip_end)
        return Interval(s, e, self.user) if s < e else None


def coalesce(intervals: List[Interval]) -> List[Interval]:
    if not intervals:
        return []
    out: List[Interval] = []
    cur = intervals[0]
    for iv in intervals[1:]:
        if iv.user == cur.user and iv.start <= cur.end:
            # contiguous or touching, merge
            cur = Interval(cur.start, max(cur.end, iv.end), cur.user)
        else:
            out.append(cur)
            cur = iv
    out.append(cur)
    return out


def generate_base(schedule: dict, from_dt: datetime, until_dt: datetime) -> List[Interval]:
    users = schedule["users"]
    if not users:
        raise ValueError("schedule.users must be non-empty")
    start_at = parse_iso8601_z(schedule["handover_start_at"])
    interval_days = int(schedule["handover_interval_days"])
    if interval_days <= 0:
        raise ValueError("handover_interval_days must be > 0")
    step = timedelta(days=interval_days)

    # Find the shift boundary at or before from_dt
    delta = from_dt - start_at
    k = delta // step  # floor division works for negatives too
    boundary = start_at + k * step
    idx = k % len(users)

    intervals: List[Interval] = []
    cur_start = boundary
    cur_idx = idx
    while cur_start < until_dt:
        cur_end = cur_start + step
        intervals.append(Interval(cur_start, cur_end, users[cur_idx]))
        cur_start = cur_end
        cur_idx = (cur_idx + 1) % len(users)

    # Possibly prepend one interval if boundary == from_dt is not strictly before from_dt
    # but we already used floor division, so boundary <= from_dt always holds.
    return intervals


def apply_overrides(base: List[Interval], overrides: List[dict]) -> List[Interval]:
    intervals = base
    for ov in overrides:
        ov_user = ov["user"]
        ov_start = parse_iso8601_z(ov["start_at"])
        ov_end = parse_iso8601_z(ov["end_at"])
        if not (ov_start < ov_end):
            continue  # ignore empty or invalid intervals

        next_intervals: List[Interval] = []
        for seg in intervals:
            if not seg.overlaps(ov_start, ov_end):
                next_intervals.append(seg)
                continue
            # left remainder
            if seg.start < ov_start:
                left_end = min(seg.end, ov_start)
                if seg.start < left_end:
                    next_intervals.append(Interval(seg.start, left_end, seg.user))
            # override portion
            mid_start = max(seg.start, ov_start)
            mid_end = min(seg.end, ov_end)
            if mid_start < mid_end:
                next_intervals.append(Interval(mid_start, mid_end, ov_user))
            # right remainder
            if ov_end < seg.end:
                right_start = max(seg.start, ov_end)
                if right_start < seg.end:
                    next_intervals.append(Interval(right_start, seg.end, seg.user))

        # Keep intervals ordered; they are appended in chronological order
        intervals = coalesce(sorted(next_intervals, key=lambda x: x.start))
    return intervals


def clamp(intervals: List[Interval], from_dt: datetime, until_dt: datetime) -> List[Interval]:
    out: List[Interval] = []
    for iv in intervals:
        clipped = iv.clipped(from_dt, until_dt)
        if clipped is not None:
            out.append(clipped)
    return coalesce(out)


def main():
    ap = argparse.ArgumentParser(description="Render final on-call schedule as JSON")
    ap.add_argument("--schedule", required=True, help="Path to schedule.json")
    ap.add_argument("--overrides", required=False, help="Path to overrides.json (array)")
    ap.add_argument("--from", dest="from_dt", required=True, help="ISO8601 Z start time")
    ap.add_argument("--until", dest="until_dt", required=True, help="ISO8601 Z end time")
    args = ap.parse_args()

    with open(args.schedule, "r", encoding="utf-8") as f:
        schedule = json.load(f)
    overrides: List[dict] = []
    if args.overrides:
        with open(args.overrides, "r", encoding="utf-8") as f:
            overrides = json.load(f)
            if not isinstance(overrides, list):
                raise ValueError("overrides must be a JSON array")

    from_dt = parse_iso8601_z(args.from_dt)
    until_dt = parse_iso8601_z(args.until_dt)
    if not (from_dt < until_dt):
        raise ValueError("--from must be earlier than --until")

    base = generate_base(schedule, from_dt, until_dt)
    final_intervals = apply_overrides(base, overrides)
    final_intervals = clamp(final_intervals, from_dt, until_dt)

    result = [
        {
            "user": iv.user,
            "start_at": isoformat_z(iv.start),
            "end_at": isoformat_z(iv.end),
        }
        for iv in final_intervals
    ]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

