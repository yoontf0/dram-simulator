"""Top-level trace-driven simulation: load CSV, run, aggregate statistics.

This module is the only entry point the Streamlit app (and tests) need.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterable, Union

from src.dram_bank import AccessType
from src.dram_config import DRAMConfig
from src.memory_controller import MemoryController, Request, RequestResult
from src.scheduler import make_scheduler

TraceSource = Union[str, Path, IO[str], Iterable[str]]


@dataclass(frozen=True)
class TraceEntry:
    """One line of the input trace: cycle, address, operation."""

    cycle: int
    address: int
    is_write: bool


@dataclass(frozen=True)
class SimulationStats:
    """Aggregate metrics over one simulation run."""

    policy: str
    total_requests: int
    total_cycles: int          # cycle at which the last data returned
    avg_latency: float
    hits: int
    misses: int
    conflicts: int
    hit_rate: float            # hits / total_requests
    bank_access_counts: dict[tuple[int, int, int], int]


@dataclass(frozen=True)
class SimulationResult:
    """Stats plus the full per-request detail for visualization."""

    stats: SimulationStats
    results: list[RequestResult]


def load_trace(source: TraceSource) -> list[TraceEntry]:
    """Parse a trace CSV with lines: cycle, address, op (READ/WRITE).

    Accepts a file path, an open text file, or an iterable of lines.
    Addresses may be decimal or hex (0x...). A header row is skipped
    automatically. Blank lines and '#' comments are ignored.
    """
    if isinstance(source, (str, Path)):
        with open(source, newline="", encoding="utf-8") as f:
            return _parse_lines(f)
    return _parse_lines(source)


def _parse_lines(lines: Iterable[str]) -> list[TraceEntry]:
    entries: list[TraceEntry] = []
    for lineno, row in enumerate(csv.reader(lines), start=1):
        if not row or row[0].strip().startswith("#"):
            continue
        if len(row) < 3:
            raise ValueError(f"trace line {lineno}: expected 'cycle,address,op', got {row}")
        cycle_s, addr_s, op_s = (col.strip() for col in row[:3])
        if lineno == 1 and not cycle_s.isdigit():
            continue  # header row
        op = op_s.upper()
        if op not in ("READ", "WRITE"):
            raise ValueError(f"trace line {lineno}: op must be READ or WRITE, got {op_s!r}")
        entries.append(
            TraceEntry(cycle=int(cycle_s), address=int(addr_s, 0), is_write=(op == "WRITE"))
        )
    return entries


def run_simulation(config: DRAMConfig, trace: list[TraceEntry], policy: str) -> SimulationResult:
    """Run the whole trace under one scheduling policy and collect stats."""
    controller = MemoryController(config, make_scheduler(policy))
    requests: list[Request] = [
        controller.make_request(i, entry.cycle, entry.address, entry.is_write)
        for i, entry in enumerate(trace)
    ]
    results = controller.run(requests)
    return SimulationResult(stats=_aggregate(results, controller.scheduler.name), results=results)


def _aggregate(results: list[RequestResult], policy: str) -> SimulationStats:
    n = len(results)
    counts = {t: 0 for t in AccessType}
    bank_counts: dict[tuple[int, int, int], int] = {}
    for r in results:
        counts[r.access_type] += 1
        key = r.request.location.bank_key
        bank_counts[key] = bank_counts.get(key, 0) + 1
    return SimulationStats(
        policy=policy,
        total_requests=n,
        total_cycles=max((r.done_cycle for r in results), default=0),
        avg_latency=(sum(r.latency for r in results) / n) if n else 0.0,
        hits=counts[AccessType.HIT],
        misses=counts[AccessType.MISS],
        conflicts=counts[AccessType.CONFLICT],
        hit_rate=(counts[AccessType.HIT] / n) if n else 0.0,
        bank_access_counts=bank_counts,
    )
