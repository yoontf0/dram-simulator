"""Educational 1T1C DRAM cell model (NOT a SPICE/transistor-level simulation).

A DRAM cell is one access transistor + one storage capacitor:

    wordline ──────┐
                   │ (gate)
    bitline ──────[T]────●────| |──── GND
                       storage   C
                        node

Concepts modeled, in normalized units (charge in [0, 1], time in abstract
"time units"):

  * Charge storage : logic 1 = charged capacitor, logic 0 = discharged.
  * Leakage        : charge decays exponentially, q(t) = q0 * exp(-dt / tau).
                     `tau` is the retention time constant.
  * Destructive read: opening the access transistor shares the cell charge
                     with the bitline, weakening it. The sense amplifier
                     detects 0/1 against a threshold, then a RESTORE
                     rewrites the full value back into the cell.
  * Refresh        : a periodic internal read+restore that tops the charge
                     back up. If refresh comes too late (charge already
                     below the sense threshold), the amplifier reads a 0
                     and faithfully restores the WRONG value -> data loss.
"""

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence


class CellEventType(Enum):
    """Things that can happen to a cell on the timeline."""

    WRITE = "WRITE"
    READ_SENSE = "READ/SENSE"    # charge sharing with the bitline + sensing
    RESTORE = "RESTORE"          # sense amp rewrites the detected value
    REFRESH = "REFRESH"          # periodic internal sense + restore
    RETENTION_FAILURE = "RETENTION FAILURE"  # sensed value != stored value


@dataclass(frozen=True)
class CellParams:
    """Knobs of the simplified 1T1C model."""

    retention_time: float = 40.0    # leakage time constant tau (time units)
    sense_threshold: float = 0.5    # min charge fraction still sensed as '1'
    read_charge_share: float = 0.35  # fraction of charge lost to the bitline per read

    def __post_init__(self) -> None:
        if self.retention_time <= 0:
            raise ValueError("retention_time must be positive")
        if not 0.0 < self.sense_threshold < 1.0:
            raise ValueError("sense_threshold must be in (0, 1)")
        if not 0.0 <= self.read_charge_share < 1.0:
            raise ValueError("read_charge_share must be in [0, 1)")


@dataclass(frozen=True)
class CellEvent:
    """One event on the cell, with charge before/after for plotting."""

    time: float
    kind: CellEventType
    charge_before: float
    charge_after: float
    sensed_value: Optional[int] = None
    note: str = ""


@dataclass(frozen=True)
class CellTimeline:
    """Full result of one cell simulation, ready for plotting."""

    samples: list[tuple[float, float]]  # (time, charge) — dense decay curve
    events: list[CellEvent]
    refresh_count: int
    failure_count: int
    final_value: int  # what a final read would return


def _decay(charge: float, dt: float, tau: float) -> float:
    """Exponential leakage over an interval of length dt."""
    return charge * math.exp(-dt / tau)


def simulate_cell(
    params: CellParams,
    *,
    write_value: int = 1,
    t_end: float = 100.0,
    read_times: Sequence[float] = (),
    refresh_interval: Optional[float] = None,
    samples_per_unit: float = 4.0,
) -> CellTimeline:
    """Simulate one cell: a WRITE at t=0, then reads/refreshes until t_end.

    `refresh_interval=None` disables refresh (shows why DRAM needs it).
    """
    if write_value not in (0, 1):
        raise ValueError("write_value must be 0 or 1")
    tau = params.retention_time

    # Build the event schedule: (time, kind) sorted by time.
    schedule: list[tuple[float, str]] = [
        (t, "read") for t in sorted(read_times) if 0.0 < t <= t_end
    ]
    if refresh_interval is not None and refresh_interval > 0:
        k = 1
        while k * refresh_interval <= t_end:
            schedule.append((k * refresh_interval, "refresh"))
            k += 1
    schedule.sort(key=lambda item: item[0])

    charge = 1.0 if write_value else 0.0
    stored_value = write_value  # what the cell is *supposed* to hold
    now = 0.0
    step = 1.0 / samples_per_unit

    samples: list[tuple[float, float]] = [(0.0, charge)]
    events: list[CellEvent] = [
        CellEvent(0.0, CellEventType.WRITE, 0.0, charge, None, f"write logic {write_value}")
    ]
    refresh_count = 0
    failure_count = 0

    def decay_to(target: float) -> None:
        """Advance time to `target`, appending dense decay samples."""
        nonlocal charge, now
        t = now + step
        while t < target:
            samples.append((t, _decay(charge, t - now, tau)))
            t += step
        charge = _decay(charge, target - now, tau)
        now = target
        samples.append((now, charge))

    for ev_time, kind in schedule:
        decay_to(ev_time)
        before = charge

        # The sense amplifier compares the (leaked) cell charge to its
        # threshold — this is the only information it has.
        sensed = 1 if before >= params.sense_threshold else 0

        if kind == "read":
            # Destructive read: charge sharing with the bitline weakens the cell.
            charge = before * (1.0 - params.read_charge_share)
            events.append(
                CellEvent(now, CellEventType.READ_SENSE, before, charge, sensed,
                          "charge shared with bitline")
            )
            samples.append((now, charge))
        else:
            refresh_count += 1

        if sensed != stored_value:
            # The value decayed below threshold before we sensed it: the
            # amplifier now believes (and will restore) the wrong value.
            failure_count += 1
            events.append(
                CellEvent(now, CellEventType.RETENTION_FAILURE, before, charge, sensed,
                          f"stored {stored_value} but sensed {sensed} — data lost")
            )
            stored_value = sensed

        # Restore / refresh: rewrite the *sensed* value at full strength.
        restored = 1.0 if sensed else 0.0
        kind_type = CellEventType.REFRESH if kind == "refresh" else CellEventType.RESTORE
        events.append(CellEvent(now, kind_type, charge, restored, sensed,
                                f"rewrite logic {sensed}"))
        charge = restored
        samples.append((now, charge))

    decay_to(t_end)
    final_value = 1 if charge >= params.sense_threshold else 0
    return CellTimeline(
        samples=samples,
        events=events,
        refresh_count=refresh_count,
        failure_count=failure_count,
        final_value=final_value,
    )


def refresh_overhead(t_rfc_cycles: int, t_refi_cycles: int) -> float:
    """Fraction of time a rank is busy refreshing: tRFC / tREFI.

    e.g. DDR4-2400: tREFI ~ 9360 cycles (7.8 us), tRFC ~ 420 cycles (350 ns)
    -> ~4.5% of all time is spent refreshing instead of serving requests.
    """
    if t_refi_cycles <= 0 or t_rfc_cycles < 0:
        raise ValueError("tREFI must be positive and tRFC non-negative")
    return t_rfc_cycles / t_refi_cycles
