"""Windows Job Object containment; worker waits for input until assignment.

ABI: Microsoft Learn jobapi2.h / winnt.h, JOBOBJECT_EXTENDED_LIMIT_INFORMATION.
Only KILL_ON_JOB_CLOSE is enabled; no breakaway, elevation or named objects.
"""

import ctypes
from ctypes import wintypes
from collections.abc import Callable
from typing import cast


def _windows_error() -> OSError:
    get_error = cast(Callable[[], int], getattr(ctypes, "get_last_error"))
    win_error = cast(Callable[[int], OSError], getattr(ctypes, "WinError"))
    return win_error(get_error())


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsJob:
    """Own a non-inheritable job handle, closed on every completion path."""

    def __init__(self) -> None:
        library = cast(Callable[..., ctypes.CDLL], getattr(ctypes, "WinDLL"))
        self.api = library("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise _windows_error()
        try:
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000
            if not self.api.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
            ):
                raise _windows_error()
        except BaseException:
            self.close()
            raise

    def assign(self, pid: int) -> None:
        # Popen still owns the unreaped worker; it cannot be a recycled PID.
        handle = self.api.OpenProcess(0x0101, False, pid)
        if not handle:
            raise _windows_error()
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise _windows_error()
        finally:
            if not self.api.CloseHandle(handle):
                raise _windows_error()

    def close(self) -> None:
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise _windows_error()
            self.handle = None
