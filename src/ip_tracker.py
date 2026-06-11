"""IP failure tracker with sliding-window counting.

Each tracked IP holds a chronological list of failure timestamps.
On every access, timestamps older than ``time_window_seconds`` are
pruned automatically.  ``is_over_threshold()`` returns ``True`` when
the surviving count reaches ``failure_threshold``.
"""

from __future__ import annotations

import time
from collections import defaultdict


class IpTracker:
    """Track RDP login failures per source IP with automatic expiry.

    Parameters
    ----------
    time_window_seconds:
        Failures older than this are ignored (sliding window).
    failure_threshold:
        Number of failures within the window that triggers a hit.
    """

    def __init__(
        self,
        time_window_seconds: float,
        failure_threshold: int,
    ) -> None:
        if time_window_seconds <= 0:
            raise ValueError("time_window_seconds must be > 0")
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")

        self._window = time_window_seconds
        self._threshold = failure_threshold
        # IP → list[float]  (monotonic timestamps, oldest → newest)
        self._failures: dict[str, list[float]] = defaultdict(list)

    # -- public API -----------------------------------------------------

    def record_failure(self, ip: str) -> int:
        """Record a failure for *ip* and return its current count (post-prune)."""
        now = time.monotonic()
        self._failures[ip].append(now)
        self._prune(ip, now)
        return len(self._failures[ip])

    def is_over_threshold(self, ip: str) -> bool:
        """Return ``True`` if *ip* has ≥ threshold recent failures."""
        self._prune(ip, time.monotonic())
        return len(self._failures.get(ip, [])) >= self._threshold

    def get_count(self, ip: str) -> int:
        """Return the current (pruned) failure count for *ip*."""
        self._prune(ip, time.monotonic())
        return len(self._failures.get(ip, []))

    def reset(self, ip: str) -> None:
        """Clear all tracked failures for *ip*."""
        self._failures.pop(ip, None)

    def get_all_ips(self) -> list[str]:
        """Return all IPs with active failures (post-prune)."""
        now = time.monotonic()
        result: list[str] = []
        for ip in list(self._failures):
            self._prune(ip, now)
            if self._failures[ip]:
                result.append(ip)
        return result

    def total_failure_count(self) -> int:
        """Return the sum of active failures across all tracked IPs."""
        now = time.monotonic()
        total = 0
        for ip in list(self._failures):
            self._prune(ip, now)
            total += len(self._failures[ip])
        return total

    def reset_all(self) -> None:
        """Clear all tracked failures for every IP."""
        self._failures.clear()

    def cleanup(self) -> int:
        """Remove entries that have zero active failures.

        Returns the number of IPs removed.  Call periodically (e.g. every
        few minutes) to prevent unbounded memory growth.
        """
        now = time.monotonic()
        removed = 0
        for ip in list(self._failures):
            self._prune(ip, now)
            if not self._failures[ip]:
                del self._failures[ip]
                removed += 1
        return removed

    @property
    def tracked_ip_count(self) -> int:
        """Number of IPs currently held (including those at zero before cleanup)."""
        return len(self._failures)

    # -- internal -------------------------------------------------------

    def _prune(self, ip: str, now: float) -> None:
        """Drop timestamps that fall outside the sliding window."""
        timestamps = self._failures.get(ip)
        if not timestamps:
            return
        cutoff = now - self._window
        # Timestamps are insertion-ordered → drop from the front.
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)
