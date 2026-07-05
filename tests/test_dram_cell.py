"""Tests for the educational 1T1C cell model."""

import math

import pytest

from src.dram_cell import (
    CellEventType,
    CellParams,
    refresh_overhead,
    simulate_cell,
)

PARAMS = CellParams(retention_time=40.0, sense_threshold=0.5, read_charge_share=0.35)


def test_charge_decays_exponentially() -> None:
    tl = simulate_cell(PARAMS, write_value=1, t_end=40.0)
    t_last, q_last = tl.samples[-1]
    assert t_last == pytest.approx(40.0)
    assert q_last == pytest.approx(math.exp(-1.0), rel=1e-6)  # q(tau) = e^-1


def test_logic_zero_stays_discharged() -> None:
    tl = simulate_cell(PARAMS, write_value=0, t_end=100.0, refresh_interval=30.0)
    assert all(q == 0.0 for _, q in tl.samples)
    assert tl.failure_count == 0
    assert tl.final_value == 0


def test_destructive_read_then_restore() -> None:
    # Read early (t=5): charge is still high, sense sees 1, restore to full.
    tl = simulate_cell(PARAMS, write_value=1, t_end=20.0, read_times=[5.0])
    read = next(e for e in tl.events if e.kind is CellEventType.READ_SENSE)
    restore = next(e for e in tl.events if e.kind is CellEventType.RESTORE)
    expected_before = math.exp(-5.0 / PARAMS.retention_time)
    assert read.charge_before == pytest.approx(expected_before, rel=1e-6)
    # Charge sharing weakened the cell...
    assert read.charge_after == pytest.approx(expected_before * 0.65, rel=1e-6)
    assert read.sensed_value == 1
    # ...then the sense amp restored the full value.
    assert restore.charge_after == 1.0
    assert tl.failure_count == 0


def test_late_read_senses_wrong_value() -> None:
    # tau=40, threshold=0.5 -> charge crosses threshold at t = 40*ln2 ~ 27.7.
    # Reading at t=60 must mis-sense a 0 and restore the wrong value.
    tl = simulate_cell(PARAMS, write_value=1, t_end=80.0, read_times=[60.0])
    assert tl.failure_count == 1
    failure = next(e for e in tl.events if e.kind is CellEventType.RETENTION_FAILURE)
    assert failure.sensed_value == 0
    assert tl.final_value == 0  # the 1 is permanently lost


def test_refresh_preserves_data() -> None:
    # Refreshing every 20 units (< 27.7 crossing point) keeps the 1 alive.
    tl = simulate_cell(PARAMS, write_value=1, t_end=200.0, refresh_interval=20.0)
    assert tl.refresh_count == 10
    assert tl.failure_count == 0
    assert tl.final_value == 1


def test_no_refresh_loses_data() -> None:
    tl = simulate_cell(PARAMS, write_value=1, t_end=200.0, refresh_interval=None,
                       read_times=[150.0])
    assert tl.failure_count == 1
    assert tl.final_value == 0


def test_too_slow_refresh_also_loses_data() -> None:
    # Refresh exists but arrives after the threshold crossing -> still fails.
    tl = simulate_cell(PARAMS, write_value=1, t_end=100.0, refresh_interval=50.0)
    assert tl.failure_count >= 1
    assert tl.final_value == 0


def test_refresh_overhead_ddr4_like() -> None:
    # DDR4-2400-ish: tREFI ~ 9360 cycles, tRFC ~ 420 cycles -> ~4.5%.
    assert refresh_overhead(420, 9360) == pytest.approx(0.0449, abs=1e-3)
    with pytest.raises(ValueError):
        refresh_overhead(420, 0)


def test_params_validation() -> None:
    with pytest.raises(ValueError):
        CellParams(retention_time=0.0)
    with pytest.raises(ValueError):
        CellParams(sense_threshold=1.5)
    with pytest.raises(ValueError):
        simulate_cell(PARAMS, write_value=2)
