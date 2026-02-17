"""CPU and memory resource limiting."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class ResourceLimits:
    """Configurable resource limits for mutation testing."""

    max_cores: int | None = None
    max_memory_percent: int | None = None

    @property
    def effective_cores(self) -> int:
        available = os.cpu_count() or 4
        if self.max_cores is not None:
            return min(self.max_cores, available)
        # Default: use half of available cores, minimum 1
        return max(1, available // 2)


def apply_cpu_limit(max_cores: int) -> None:
    """Restrict this process to a set of CPU cores."""
    available = os.cpu_count() or 4
    cores = min(max_cores, available)
    try:
        os.sched_setaffinity(0, set(range(cores)))
    except (AttributeError, OSError):
        # Not available on all platforms (e.g., macOS)
        pass


def check_memory_usage() -> float:
    """Return current memory usage as a percentage (0-100)."""
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        mem_total = 0
        mem_available = 0
        for line in lines:
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available = int(line.split()[1])
        if mem_total > 0:
            return (1 - mem_available / mem_total) * 100.0
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def is_memory_ok(limits: ResourceLimits) -> bool:
    """Check if memory usage is within configured limits."""
    if limits.max_memory_percent is None:
        return True
    return check_memory_usage() < limits.max_memory_percent


def apply_limits(limits: ResourceLimits) -> None:
    """Apply configured resource limits to the current process."""
    if limits.max_cores is not None:
        apply_cpu_limit(limits.max_cores)
