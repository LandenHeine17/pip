"""The Pi's health for the monitor's "Pi health" box (common/vitals.py, D-111): processor, memory, temperature and SD
card, read from fake /proc and /sys files; anything it can't read is None, never an error."""
import io
import shutil
from collections import namedtuple

from pip_assistant.common import vitals

FILES = {
    "/proc/stat": "cpu  100 0 100 700 100 0 0 0 0 0\n",            # busy 200 of 1000 since the Pi started
    "/proc/meminfo": "MemTotal:        8000000 kB\nMemFree:  1000000 kB\nMemAvailable:    6000000 kB\n",
    "/sys/class/thermal/thermal_zone0/temp": "54321\n",
}


def fake_open(files):
    def opener(path, *a, **kw):
        if path not in files:
            raise FileNotFoundError(path)
        return io.StringIO(files[path])
    return opener


def test_it_reads_the_four_numbers(monkeypatch):
    monkeypatch.setattr(vitals, "open", fake_open(FILES), raising=False)
    monkeypatch.setattr(vitals, "_last", None)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(200, 50, 150))
    v = vitals.read()
    assert v["cpu"] == 20.0 and v["memory"] == 25.0 and v["temp_c"] == 54.3 and v["disk"] == 25.0 and v["at"] > 0


def test_the_processor_is_measured_since_the_last_reading(monkeypatch):
    files = dict(FILES)
    monkeypatch.setattr(vitals, "open", fake_open(files), raising=False)
    monkeypatch.setattr(vitals, "_last", None)
    vitals.read()
    files["/proc/stat"] = "cpu  190 0 110 800 100 0 0 0 0 0\n"   # since then: 100 busy of 200
    assert vitals.read()["cpu"] == 50.0


def test_what_it_cannot_read_is_none(monkeypatch):
    monkeypatch.setattr(vitals, "open", fake_open({}), raising=False)
    monkeypatch.setattr(vitals, "_last", None)

    def no_disk(path):
        raise OSError("no disk")
    monkeypatch.setattr(shutil, "disk_usage", no_disk)
    v = vitals.read()
    assert (v["cpu"], v["memory"], v["temp_c"], v["disk"]) == (None, None, None, None)
