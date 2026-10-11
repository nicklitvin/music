"""Logging for OMR jobs: what each job and page spent its time on, and how
the machine was coping, so a slow sheet can be explained after the fact.

Lines go to stderr prefixed "omr", which under PM2 lands in its error log.
On the server:

    pm2 logs music-backend --lines 300 --nostream | grep omr

A running job logs a heartbeat once a minute (see jobs.py) with the CPU it
is getting and the machine's free memory, swap, load and CPU steal --
steal is the share of CPU the hypervisor withheld, which is what a
burstable (t3) instance out of CPU credits looks like.
"""

import logging
import os
import sys
import warnings

try:
    import resource
except ImportError:  # Windows
    resource = None

logger = logging.getLogger("omr")


def configure() -> None:
    """Called once per process (the web server, and each spawned worker)."""
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s omr %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # oemer's classifiers were pickled by an older scikit-learn, which warns
    # about it on every load -- dozens of lines a page, drowning out the rest.
    warnings.filterwarnings("ignore", message="Trying to unpickle estimator")


def peak_rss_gb() -> float | None:
    """This process's peak resident memory so far (not on Windows)."""
    if resource is None:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1024**2 if sys.platform != "darwin" else peak / 1024**3


def process_memory_gb(pid: int) -> tuple[float, float] | None:
    """(resident, peak resident) of a process, Linux only."""
    try:
        values = {}
        with open(f"/proc/{pid}/status") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in ("VmRSS", "VmHWM"):
                    values[key] = int(rest.split()[0]) / 1024**2
        return values["VmRSS"], values["VmHWM"]
    except (OSError, KeyError, ValueError):
        return None


class MachineSampler:
    """Free memory, swap, load and CPU steal since the previous sample
    (Linux; elsewhere it reports nothing)."""

    def __init__(self) -> None:
        self._last = self._cpu_times()

    @staticmethod
    def _cpu_times() -> tuple[int, int] | None:
        try:
            with open("/proc/stat") as handle:
                fields = [int(value) for value in handle.readline().split()[1:]]
            # user nice system idle iowait irq softirq steal ...
            return sum(fields[:8]), fields[7]
        except (OSError, ValueError, IndexError):
            return None

    def sample(self) -> str:
        parts = []
        try:
            info = {}
            with open("/proc/meminfo") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    info[key] = int(rest.split()[0]) / 1024**2
            parts.append(f"free {info['MemAvailable']:.1f} GB")
            parts.append(f"swap {info['SwapTotal'] - info['SwapFree']:.2f} GB")
        except (OSError, KeyError, ValueError):
            pass
        try:
            parts.append(f"load {os.getloadavg()[0]:.1f}")
        except (AttributeError, OSError):
            pass
        now = self._cpu_times()
        if now and self._last and now[0] > self._last[0]:
            steal = (now[1] - self._last[1]) / (now[0] - self._last[0])
            parts.append(f"steal {steal:.0%}")
        self._last = now
        return ", ".join(parts) if parts else "no machine stats here"
