"""A process's memory, read with nothing but the standard library (P-10, P-49), so a measuring process can hold only
what it measures: evals.memory_cost and evals.citation.memory both use it."""

from __future__ import annotations

import sys
from pathlib import Path

MB = 1024 * 1024


def memory_bytes() -> tuple[int, int]:
    """(peak, now): the process's peak memory so far and its memory now (working set on Windows; elsewhere the max
    resident set, and the resident set from /proc where there is one)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        read = kernel.K32GetProcessMemoryInfo
        read.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        read.restype = wintypes.BOOL
        if not read(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")  # type: ignore[attr-defined]
        return int(counters.PeakWorkingSetSize), int(counters.WorkingSetSize)
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = int(rss if sys.platform == "darwin" else rss * 1024)
    try:
        pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
        import os

        return peak, pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return peak, peak
