"""Tests for the DRAM simulator core (no Streamlit involved)."""

from pathlib import Path

import pytest

from src.address_mapper import AddressMapper
from src.dram_bank import AccessType, Bank, CommandType
from src.dram_config import DRAMConfig
from src.simulator import TraceEntry, load_trace, run_simulation

CFG = DRAMConfig()  # 8 banks, tRCD=14, tCL=14, tRP=14, tRAS=33, tRC=47
DATA = Path(__file__).parent.parent / "data" / "sample_trace.csv"


# ---------------- Address mapping ----------------

def test_address_mapper_roundtrip() -> None:
    mapper = AddressMapper(CFG)
    addr = mapper.compose(channel=0, rank=0, bank=3, row=42, column=17)
    loc = mapper.decompose(addr)
    assert (loc.channel, loc.rank, loc.bank, loc.row, loc.column) == (0, 0, 3, 42, 17)


def test_address_mapper_known_layout() -> None:
    # Default config: 10 column bits, 0 channel bits, 3 bank bits, 0 rank bits.
    mapper = AddressMapper(CFG)
    loc = mapper.decompose(0xA400)  # bank = (0xA400>>10)&7 = 1, row = 0xA400>>13 = 5
    assert loc.bank == 1 and loc.row == 5 and loc.column == 0


def test_consecutive_addresses_share_a_row() -> None:
    mapper = AddressMapper(CFG)
    a, b = mapper.decompose(0x100), mapper.decompose(0x140)
    assert a.bank_key == b.bank_key and a.row == b.row and a.column != b.column


# ---------------- Bank state machine & timing ----------------

def test_first_access_is_miss_with_trcd_tcl_latency() -> None:
    bank = Bank(CFG, (0, 0, 0))
    atype, cmds, done = bank.access(row=7, column=0, is_write=False, cycle=0)
    assert atype is AccessType.MISS
    assert [c.command for c in cmds] == [CommandType.ACTIVATE, CommandType.READ]
    assert done == CFG.tRCD + CFG.tCL


def test_row_hit_latency_is_tcl() -> None:
    bank = Bank(CFG, (0, 0, 0))
    bank.access(row=7, column=0, is_write=False, cycle=0)
    atype, cmds, done = bank.access(row=7, column=8, is_write=False, cycle=1000)
    assert atype is AccessType.HIT
    assert [c.command for c in cmds] == [CommandType.READ]
    assert done == 1000 + CFG.tCL


def test_row_conflict_latency_is_trp_trcd_tcl() -> None:
    bank = Bank(CFG, (0, 0, 0))
    bank.access(row=7, column=0, is_write=False, cycle=0)
    # Arrive long after tRAS/tRC expire so only tRP+tRCD+tCL remain.
    atype, cmds, done = bank.access(row=9, column=0, is_write=False, cycle=1000)
    assert atype is AccessType.CONFLICT
    assert [c.command for c in cmds] == [
        CommandType.PRECHARGE, CommandType.ACTIVATE, CommandType.READ
    ]
    assert done == 1000 + CFG.tRP + CFG.tRCD + CFG.tCL


def test_tras_delays_early_precharge() -> None:
    bank = Bank(CFG, (0, 0, 0))
    bank.access(row=7, column=0, is_write=False, cycle=0)  # ACTIVATE at cycle 0
    # Conflict immediately after: PRECHARGE must wait until tRAS.
    _, cmds, _ = bank.access(row=9, column=0, is_write=False, cycle=1)
    pre = next(c for c in cmds if c.command is CommandType.PRECHARGE)
    act = next(c for c in cmds if c.command is CommandType.ACTIVATE)
    assert pre.cycle >= CFG.tRAS
    assert act.cycle >= CFG.tRC  # ACT-to-ACT same bank


def test_write_uses_write_command() -> None:
    bank = Bank(CFG, (0, 0, 0))
    _, cmds, _ = bank.access(row=1, column=2, is_write=True, cycle=0)
    assert cmds[-1].command is CommandType.WRITE


# ---------------- End-to-end simulation ----------------

def test_sample_trace_runs_end_to_end() -> None:
    trace = load_trace(DATA)
    assert len(trace) == 20
    sim = run_simulation(CFG, trace, "FCFS")
    assert sim.stats.total_requests == 20
    assert sim.stats.hits + sim.stats.misses + sim.stats.conflicts == 20
    assert sim.stats.total_cycles > 0
    assert all(r.latency > 0 for r in sim.results)


def test_frfcfs_beats_fcfs_on_bursty_conflicting_trace() -> None:
    """Same-cycle burst alternating rows in one bank: FR-FCFS should
    group the row hits and win on both hit rate and total cycles."""
    mapper = AddressMapper(CFG)
    row_a = mapper.compose(0, 0, 0, row=0, column=0)
    row_b = mapper.compose(0, 0, 0, row=2, column=0)
    trace = [
        TraceEntry(cycle=0, address=addr, is_write=False)
        for addr in [row_a, row_b, row_a + 0x40, row_b + 0x40, row_a + 0x80, row_b + 0x80]
    ]
    fcfs = run_simulation(CFG, trace, "FCFS").stats
    frfcfs = run_simulation(CFG, trace, "FR-FCFS").stats
    assert frfcfs.hits > fcfs.hits
    assert frfcfs.total_cycles < fcfs.total_cycles


def test_trace_parser_accepts_header_hex_and_comments() -> None:
    lines = [
        "cycle,address,op",
        "# comment line",
        "0,0x40,READ",
        "5,64,WRITE",
        "",
    ]
    trace = load_trace(lines)
    assert trace == [
        TraceEntry(cycle=0, address=0x40, is_write=False),
        TraceEntry(cycle=5, address=64, is_write=True),
    ]


def test_trace_parser_rejects_bad_op() -> None:
    with pytest.raises(ValueError, match="op must be READ or WRITE"):
        load_trace(["0,0x0,FETCH"])
