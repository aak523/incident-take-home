#!/usr/bin/env python3
import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Tuple


# ISO8601 format with 'Z' suffix for UTC timestamps
ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def parse_iso8601_z(s: str) -> datetime:
    """
    Parse ISO8601 timestamp string to timezone-aware datetime in UTC.
    """
    if s.endswith("Z"):
        # Strict 'Z' handling; ensure UTC tz-aware
        return datetime.strptime(s, ISO_FMT).replace(tzinfo=timezone.utc)
    # Fallback: try fromisoformat to support offsets if provided
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # Assume UTC if naive (inputs are expected in Z)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    """
    Format datetime as ISO8601 string with 'Z' suffix (UTC).
    """
    return dt.astimezone(timezone.utc).strftime(ISO_FMT)


@dataclass
class Interval:
    """
    Represents a contiguous time interval with an assigned user.
    
    This is the core data structure for representing on-call shifts.
    Intervals are half-open: [start, end), meaning they include start
    but exclude end to avoid ambiguity at handover boundaries.
    
    Attributes:
        start: Beginning of the interval (inclusive)
        end: End of the interval (exclusive)
        user: Username of the person on-call during this interval
    """
    start: datetime
    end: datetime
    user: str

    def overlaps(self, other_start: datetime, other_end: datetime) -> bool:
        """
        Check if this interval overlaps with another time range.
        
        Uses half-open interval logic: intervals overlap if they share
        any point in time, excluding boundary points.
        """
        return self.start < other_end and other_start < self.end

    def clipped(self, clip_start: datetime, clip_end: datetime):
        """
        Return a new interval clipped to the given time range.
        
        This is used to truncate schedule entries to the requested
        --from and --until window.
        """
        s = max(self.start, clip_start)
        e = min(self.end, clip_end)
        return Interval(s, e, self.user) if s < e else None


def coalesce(intervals: List[Interval]) -> List[Interval]:
    """
    Merge adjacent or overlapping intervals with the same user.
    
    This keeps the output compact and intuitive by combining consecutive
    shifts for the same person into a single entry. For example, if Alice
    has shifts 5pm-7pm and 7pm-9pm, they merge to 5pm-9pm.
    
    Precondition: intervals must be sorted by start time.
    
    Args:
        intervals: List of intervals sorted chronologically
        
    Returns:
        New list with adjacent same-user intervals merged
    """
    if not intervals:
        return []
    out: List[Interval] = []
    cur = intervals[0]
    for iv in intervals[1:]:
        if iv.user == cur.user and iv.start <= cur.end:
            # Contiguous or touching intervals with same user - merge them
            cur = Interval(cur.start, max(cur.end, iv.end), cur.user)
        else:
            # Different user or gap - start new interval
            out.append(cur)
            cur = iv
    out.append(cur)
    return out


def generate_base(schedule: dict, from_dt: datetime, until_dt: datetime) -> List[Interval]:
    """
    Generate base rotating schedule from configuration.
    
    Creates the foundational schedule where users rotate through on-call shifts
    at regular intervals (e.g., weekly). The rotation starts from handover_start_at
    and continues indefinitely in a round-robin fashion.
    
    Algorithm: Uses floor division to deterministically find the shift boundary
    at or before from_dt, ensuring exact alignment with handover times. This
    avoids cumulative floating-point errors and provides O(K) complexity where
    K is the number of shifts intersecting the requested window.
    
    Args:
        schedule: Schedule configuration with users, handover_start_at, and interval
        from_dt: Start of requested time window
        until_dt: End of requested time window
        
    Returns:
        List of intervals covering from_dt to until_dt (may extend beyond)
        
    Raises:
        ValueError: If users list is empty or interval_days <= 0
    """
    users = schedule["users"]
    if not users:
        raise ValueError("schedule.users must be non-empty")
    start_at = parse_iso8601_z(schedule["handover_start_at"])
    interval_days = int(schedule["handover_interval_days"])
    if interval_days <= 0:
        raise ValueError("handover_interval_days must be > 0")
    step = timedelta(days=interval_days)

    # Find the shift boundary at or before from_dt
    # Uses floor division which works correctly for negative deltas too
    delta = from_dt - start_at
    k = delta // step  # Number of complete intervals since start_at
    boundary = start_at + k * step  # Shift boundary at or before from_dt
    idx = k % len(users)  # Which user is on-call at this boundary

    # Generate intervals starting from boundary until we cover until_dt
    intervals: List[Interval] = []
    cur_start = boundary
    cur_idx = idx
    while cur_start < until_dt:
        cur_end = cur_start + step
        intervals.append(Interval(cur_start, cur_end, users[cur_idx]))
        cur_start = cur_end
        cur_idx = (cur_idx + 1) % len(users)  # Rotate to next user

    return intervals


def split_interval_by_override(
    seg: Interval, ov_start: datetime, ov_end: datetime, ov_user: str
) -> List[Interval]:
    """
    Split a single interval by an override, returning up to 3 pieces.
    
    When an override overlaps with a base interval, we split it into:
    1. Left remainder: base user continues before override starts
    2. Override portion: override user takes over for the overlap
    3. Right remainder: base user resumes after override ends
    
    If there's no overlap, the original interval is returned unchanged.
    This ensures no gaps or overlaps in coverage.
    
    Args:
        seg: The base interval to potentially split
        ov_start: Override start time
        ov_end: Override end time
        ov_user: User who takes over during the override
        
    Returns:
        List of 1-3 intervals in chronological order
    """
    if not seg.overlaps(ov_start, ov_end):
        # No overlap - return original interval unchanged
        return [seg]
    
    pieces: List[Interval] = []
    
    # Left remainder: portion before override starts
    if seg.start < ov_start:
        left_end = min(seg.end, ov_start)
        if seg.start < left_end:
            pieces.append(Interval(seg.start, left_end, seg.user))
    
    # Override portion: where override and base interval overlap
    mid_start = max(seg.start, ov_start)
    mid_end = min(seg.end, ov_end)
    if mid_start < mid_end:
        pieces.append(Interval(mid_start, mid_end, ov_user))
    
    # Right remainder: portion after override ends
    if ov_end < seg.end:
        right_start = max(seg.start, ov_end)
        if right_start < seg.end:
            pieces.append(Interval(right_start, seg.end, seg.user))
    
    return pieces


def apply_single_override(
    intervals: List[Interval], ov_start: datetime, ov_end: datetime, ov_user: str
) -> List[Interval]:
    """
    Apply a single override to a list of intervals.
    
    Sweeps through all intervals, splitting any that overlap with the override.
    The result is a new list of intervals where the override has been applied
    and the schedule remains gapless and overlap-free.
    
    Args:
        intervals: Current list of schedule intervals
        ov_start: Override start time
        ov_end: Override end time
        ov_user: User taking the override shift
        
    Returns:
        New list of intervals with override applied, sorted and coalesced
    """
    if not (ov_start < ov_end):
        # Ignore empty or invalid intervals
        return intervals
    
    next_intervals: List[Interval] = []
    for seg in intervals:
        # Split each interval by the override (returns 1-3 pieces)
        pieces = split_interval_by_override(seg, ov_start, ov_end, ov_user)
        next_intervals.extend(pieces)
    
    # Keep intervals ordered and coalesce adjacent intervals with same user
    return coalesce(sorted(next_intervals, key=lambda x: x.start))


def apply_overrides(base: List[Interval], overrides: List[dict]) -> List[Interval]:
    """
    Apply all overrides sequentially to the base schedule.
    
    Overrides are applied in order, with later overrides taking precedence
    over earlier ones when they overlap. This provides a predictable,
    stable rule commonly used in scheduling systems.
    
    The algorithm supports arbitrary overlap patterns:
    - Partial overlaps (override covers part of a shift)
    - Multiple overlaps (one override affects multiple shifts)
    - Cascading overrides (overrides that overlap each other)
    - Any user can override (not limited to users in base rotation)
    
    Complexity: O(N * M) worst case where N is base intervals and M is
    number of overrides. In practice N is small (windowed by from/until).
    
    Args:
        base: Base rotating schedule intervals
        overrides: List of override dictionaries with user, start_at, end_at
        
    Returns:
        Final schedule with all overrides applied
    """
    intervals = base
    for ov in overrides:
        ov_user = ov["user"]
        ov_start = parse_iso8601_z(ov["start_at"])
        ov_end = parse_iso8601_z(ov["end_at"])
        intervals = apply_single_override(intervals, ov_start, ov_end, ov_user)
    return intervals


def clamp(intervals: List[Interval], from_dt: datetime, until_dt: datetime) -> List[Interval]:
    """
    Truncate intervals to the requested time window.
    
    Clips each interval to [from_dt, until_dt), removing any portions
    that fall outside this range. As per requirements, if an entry spans
    beyond the window, only the portion within the window is returned.
    
    Args:
        intervals: List of intervals to truncate
        from_dt: Start of requested window (inclusive)
        until_dt: End of requested window (exclusive)
        
    Returns:
        Intervals clipped to window, coalesced to remove redundant boundaries
    """
    out: List[Interval] = []
    for iv in intervals:
        clipped = iv.clipped(from_dt, until_dt)
        if clipped is not None:
            out.append(clipped)
    return coalesce(out)


def main():
    """
    Main entry point for the schedule rendering script.
    
    Implements the three-phase algorithm:
    1. Generate base rotating schedule from configuration
    2. Apply temporary overrides (people covering for each other)
    3. Truncate to requested time window
    
    Outputs final schedule as JSON array of entries with user, start_at, end_at.
    """
    ap = argparse.ArgumentParser(description="Render final on-call schedule as JSON")
    ap.add_argument("--schedule", required=True, help="Path to schedule.json")
    ap.add_argument("--overrides", required=False, help="Path to overrides.json (array)")
    ap.add_argument("--from", dest="from_dt", required=True, help="ISO8601 Z start time")
    ap.add_argument("--until", dest="until_dt", required=True, help="ISO8601 Z end time")
    args = ap.parse_args()

    # Load schedule configuration
    with open(args.schedule, "r", encoding="utf-8") as f:
        schedule = json.load(f)
    
    # Load overrides (optional)
    overrides: List[dict] = []
    if args.overrides:
        with open(args.overrides, "r", encoding="utf-8") as f:
            overrides = json.load(f)
            if not isinstance(overrides, list):
                raise ValueError("overrides must be a JSON array")

    # Parse and validate time window
    from_dt = parse_iso8601_z(args.from_dt)
    until_dt = parse_iso8601_z(args.until_dt)
    if not (from_dt < until_dt):
        raise ValueError("--from must be earlier than --until")

    # Phase 1: Generate base rotating schedule
    base = generate_base(schedule, from_dt, until_dt)
    
    # Phase 2: Apply overrides via interval splitting
    final_intervals = apply_overrides(base, overrides)
    
    # Phase 3: Truncate to requested window
    final_intervals = clamp(final_intervals, from_dt, until_dt)

    # Convert to output format and print
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

