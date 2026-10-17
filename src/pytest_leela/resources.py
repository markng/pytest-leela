"""CPU and memory resource limiting."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# On Python <=3.13, this lets an invalid ``BitOr -> BitAnd`` annotation
# mutation fail while the definition is executed. Python 3.14 uses PEP 649
# lazy annotations instead; the annotation-policy regression reads supported
# hints to exercise that failure. See ``pytest_leela.import_hook`` for why
# its ``compile()`` call must also remain unflagged.

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
    setaffinity = getattr(os, "sched_setaffinity", None)
    if setaffinity is None:
        # Not available on all platforms (e.g., macOS)
        return
    try:
        setaffinity(0, set(range(cores)))
    except (AttributeError, OSError):
        # ``AttributeError`` covers platforms where the attribute
        # disappears between the ``getattr`` lookup and the call
        # (e.g. mocked test environments that remove the attr on
        # call). ``OSError`` covers kernels that refuse affinity
        # changes (containers, seccomp, etc.).
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
