import ctypes
import platform

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.c_uint),
        ('dwTime', ctypes.c_ulong)
    ]

def get_user_idle_time_seconds() -> float:
    """
    Returns the number of seconds the user has been idle (no mouse/keyboard input).
    Currently supports Windows. Returns 0.0 on other platforms or error.
    """
    system_name = platform.system()
    
    if system_name != 'Windows':
        return 0.0
        
    try:
        lastInputInfo = LASTINPUTINFO()
        lastInputInfo.cbSize = ctypes.sizeof(lastInputInfo)
        
        # GetLastInputInfo returns non-zero on success
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lastInputInfo)):
            # GetTickCount() returns milliseconds since system start
            # dwTime is the tick count when the last input event was received
            millis = ctypes.windll.kernel32.GetTickCount() - lastInputInfo.dwTime
            return millis / 1000.0
        else:
            return 0.0
    except Exception:
        return 0.0
