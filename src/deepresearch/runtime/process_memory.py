"""Windows read-only counters; only caller-provided project-owned PIDs are sampled."""
import ctypes
from ctypes import wintypes


class PerformanceInfo(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in (
        'CommitTotal', 'CommitLimit', 'CommitPeak', 'PhysicalTotal', 'PhysicalAvailable',
        'SystemCache', 'KernelTotal', 'KernelPaged', 'KernelNonpaged', 'PageSize')] + [
        (name, wintypes.DWORD) for name in ('HandleCount', 'ProcessCount', 'ThreadCount')]


class ProcessMemory(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
        'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
        'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


def system_commit():
    info = PerformanceInfo(); info.cb = ctypes.sizeof(info)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    if not psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {'system_committed_bytes': info.CommitTotal * info.PageSize,
            'system_commit_limit_bytes': info.CommitLimit * info.PageSize,
            'system_commit_peak_bytes': info.CommitPeak * info.PageSize}


def process_memory(pid, role):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return {'pid': pid, 'role': role, 'status': 'unavailable', 'win_error': ctypes.get_last_error()}
    try:
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
        info = ProcessMemory(); info.cb = ctypes.sizeof(info)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(info), info.cb):
            return {'pid': pid, 'role': role, 'status': 'unavailable', 'win_error': ctypes.get_last_error()}
        creation, exit_time, kernel_time, user_time = [ctypes.c_ulonglong() for _ in range(4)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(v) for v in (creation, exit_time, kernel_time, user_time)]):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'pid': pid, 'role': role, 'status': 'sampled',
                'working_set_bytes': info.WorkingSetSize, 'private_bytes': info.PrivateUsage,
                'peak_working_set_bytes': info.PeakWorkingSetSize, 'creation_filetime': creation.value}
    finally:
        kernel.CloseHandle(handle)
