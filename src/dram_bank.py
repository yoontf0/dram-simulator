"""Per-bank state machine and timing model.

A bank tracks its open row and the timing history needed to enforce:
  - tRCD : ACTIVATE -> READ/WRITE
  - tCL  : READ/WRITE -> data on the bus (used for both READ and WRITE in MVP)
  - tRP  : PRECHARGE -> ACTIVATE
  - tRAS : ACTIVATE -> PRECHARGE (row must stay open at least this long)
  - tRC  : ACTIVATE -> ACTIVATE, same bank

Resulting unconstrained access latencies:
  Row HIT      : tCL
  Row MISS     : tRCD + tCL              (bank was precharged / no open row)
  Row CONFLICT : tRP + tRCD + tCL        (another row was open)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.dram_config import DRAMConfig


class AccessType(Enum):
    """Row-buffer outcome for one memory request."""

    HIT = "hit"           # requested row already open
    MISS = "miss"         # bank has no open row (row-buffer empty)
    CONFLICT = "conflict"  # a different row is open and must be precharged


class CommandType(Enum):
    """DRAM commands the controller can issue to a bank."""

    ACTIVATE = "ACTIVATE"
    READ = "READ"
    WRITE = "WRITE"
    PRECHARGE = "PRECHARGE"


@dataclass(frozen=True)
class Command:
    """One DRAM command issued at a specific cycle."""

    cycle: int
    command: CommandType
    bank_key: tuple[int, int, int]
    row: int
    column: Optional[int] = None  # only meaningful for READ/WRITE


class Bank:
    """State machine for a single DRAM bank (open-page policy)."""

    # A large negative sentinel so the very first ACTIVATE is unconstrained.
    _NEVER = -(10**9)

    def __init__(self, config: DRAMConfig, bank_key: tuple[int, int, int]) -> None:
        self.config = config
        self.bank_key = bank_key
        self.open_row: Optional[int] = None
        self.last_activate: int = Bank._NEVER  # cycle of the most recent ACTIVATE
        self.ready: int = 0                    # earliest cycle this bank accepts a command

    def classify(self, row: int) -> AccessType:
        """Row-buffer outcome if `row` were accessed right now."""
        if self.open_row is None:
            return AccessType.MISS
        if self.open_row == row:
            return AccessType.HIT
        return AccessType.CONFLICT

    def access(
        self, row: int, column: int, is_write: bool, cycle: int
    ) -> tuple[AccessType, list[Command], int]:
        """Service one request starting no earlier than `cycle`.

        Returns (access_type, issued commands, data_ready_cycle).
        Mutates bank state (open row / timing history).
        """
        cfg = self.config
        start = max(cycle, self.ready)
        access_type = self.classify(row)
        commands: list[Command] = []

        if access_type is AccessType.CONFLICT:
            # Must close the open row first; PRECHARGE cannot come before tRAS.
            pre_cycle = max(start, self.last_activate + cfg.tRAS)
            commands.append(Command(pre_cycle, CommandType.PRECHARGE, self.bank_key, self.open_row))  # type: ignore[arg-type]
            act_cycle = max(pre_cycle + cfg.tRP, self.last_activate + cfg.tRC)
            commands.append(Command(act_cycle, CommandType.ACTIVATE, self.bank_key, row))
            rw_cycle = act_cycle + cfg.tRCD
            self.last_activate = act_cycle
        elif access_type is AccessType.MISS:
            # Bank is idle; only tRC against the previous ACTIVATE applies.
            act_cycle = max(start, self.last_activate + cfg.tRC)
            commands.append(Command(act_cycle, CommandType.ACTIVATE, self.bank_key, row))
            rw_cycle = act_cycle + cfg.tRCD
            self.last_activate = act_cycle
        else:  # HIT: the row is already open, go straight to the column access.
            rw_cycle = start

        rw_type = CommandType.WRITE if is_write else CommandType.READ
        commands.append(Command(rw_cycle, rw_type, self.bank_key, row, column))
        data_ready = rw_cycle + cfg.tCL

        self.open_row = row
        self.ready = rw_cycle + 1  # next command to this bank (tCCD ignored in MVP)
        return access_type, commands, data_ready
