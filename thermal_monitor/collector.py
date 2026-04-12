from __future__ import annotations
import concurrent.futures
import logging
import random
import time
from dataclasses import replace
from typing import Dict, List
from thermal_monitor.models import ThermalReading
from thermal_monitor.sources.base import ThermalSource

log = logging.getLogger(__name__)


def _apply_sensor_thresholds(
    readings: List[ThermalReading],
    overrides: Dict[str, Dict],
    source_name: str,
) -> List[ThermalReading]:
    """
    Apply per-sensor warn/crit overrides from sensor_thresholds config.

    Matching order (first match wins):
      1. Exact key match              — "54-P/S 1 Inlet": {warn: 45}
      2. Longest substring key match  — "P/S": {warn: 45}  matches any name
                                        containing "P/S"

    Substring keys let you set defaults for a whole sensor family (e.g. all
    P/S or PS inlet sensors) without listing every sensor name explicitly.
    Exact names always take precedence over substring patterns.

    Returns a new list; originals are unchanged.
    """
    result = []
    for r in readings:
        # Exact match first, then longest substring match.
        override = overrides.get(r.sensor)
        if override is None:
            matches = [k for k in overrides if k in r.sensor]
            if matches:
                override = overrides[max(matches, key=len)]
        if override:
            new_warn = float(override["warn"]) if "warn" in override else r.warn
            new_crit = float(override["crit"]) if "crit" in override else r.crit
            if new_warn >= new_crit:
                log.warning("[%s] sensor_thresholds for %r: warn (%.0f) >= crit (%.0f) — override ignored",
                            source_name, r.sensor, new_warn, new_crit)
                result.append(r)
            else:
                log.debug("[%s] override thresholds for %r: warn=%.0f crit=%.0f",
                          source_name, r.sensor, new_warn, new_crit)
                result.append(replace(r, warn=new_warn, crit=new_crit))
        else:
            result.append(r)
    return result


def _collect_one(src: ThermalSource, jitter: float = 0.0) -> List[ThermalReading]:
    """
    Collect from a single source; never raises.

    Args:
        jitter: Upper bound of a uniform random pre-delay in seconds.
                Each worker independently draws from [0, jitter] before
                opening its connection, spreading load on shared resources.
    """
    if jitter > 0:
        delay = random.uniform(0, jitter)
        log.debug("[%s] jitter delay %.2fs", src.name, delay)
        time.sleep(delay)
    log.debug("--- collecting from source: %s (%s) ---", src.name, type(src).__name__)
    try:
        readings = src.collect()
        if src.sensor_thresholds:
            readings = _apply_sensor_thresholds(readings, src.sensor_thresholds, src.name)
        return readings
    except Exception as exc:   # pragma: no cover — belt-and-suspenders
        log.exception("Unhandled error in source %r", src.name)
        return [ThermalReading(
            source=src.name, sensor="(crash)",
            value=0.0, warn=src.warn, crit=src.crit,
            error=f"Unhandled exception: {exc}",
        )]


def collect_all(
    sources: List[ThermalSource],
    max_workers: int = 0,
    jitter: float = 0.0,
) -> List[ThermalReading]:
    """
    Collect from all sources in parallel using a thread pool.

    Each source is I/O-bound (subprocess, network), so threading gives a
    near-linear speedup — 10 sources with 30 s timeouts finish in ~30 s
    instead of ~300 s.

    Args:
        max_workers: Maximum parallel threads.  0 = one thread per source.
        jitter:      Each worker sleeps a random duration in [0, jitter]
                     seconds before starting its connection.  Use this to
                     avoid bursting traffic on a shared management network.
                     0.0 (default) disables jitter.
    """
    if not sources:
        return []
    workers = max_workers if max_workers > 0 else len(sources)
    if jitter > 0:
        log.debug("collection: %d source(s), %d worker(s), jitter up to %.1fs",
                  len(sources), workers, jitter)
    else:
        log.debug("collection: %d source(s), %d worker(s), no jitter",
                  len(sources), workers)
    t0 = time.monotonic()

    readings: List[ThermalReading] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_collect_one, src, jitter): src for src in sources}
        for future in concurrent.futures.as_completed(futures):
            readings.extend(future.result())

    elapsed = time.monotonic() - t0
    log.debug("collection complete: %d reading(s) from %d source(s) in %.1fs",
              len(readings), len(sources), elapsed)
    return readings
