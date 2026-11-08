"""The Pi's health for the monitor's "Pi health" box (D-111): how busy the processor is, memory in use,
how warm it is, and how full the SD card is. Read from the Pi itself; nothing personal."""
import os
import shutil
import time

_last: tuple[float, float] | None = None   # (busy, total) processor time at the last reading


def _cpu() -> float | None:
    """Percent busy since the last reading (the first reading: since the Pi started)."""
    global _last
    try:
        with open("/proc/stat") as f:
            parts = [float(x) for x in f.readline().split()[1:]]
    except OSError:
        return None
    idle, total = parts[3] + (parts[4] if len(parts) > 4 else 0), sum(parts)
    busy = total - idle
    before, _last = _last, (busy, total)
    if before and total > before[1]:
        busy, total = busy - before[0], total - before[1]
    return round(100 * busy / total, 1) if total else None


def _memory() -> float | None:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, value = line.split(":", 1)
                info[key] = float(value.split()[0])
        return round(100 * (1 - info["MemAvailable"] / info["MemTotal"]), 1)
    except (OSError, KeyError, ValueError):
        return None


def _temperature() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def _disk(path: str = os.path.expanduser("~")) -> float | None:
    try:
        u = shutil.disk_usage(path)
        return round(100 * u.used / u.total, 1)
    except OSError:
        return None


def read() -> dict:
    """{"cpu": %, "memory": %, "temp_c": °C, "disk": %, "at": time}; a part it can't read is None."""
    return {"cpu": _cpu(), "memory": _memory(), "temp_c": _temperature(), "disk": _disk(), "at": time.time()}
