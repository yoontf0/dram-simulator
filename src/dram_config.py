"""DRAM organization and timing parameters.

All timing values are expressed in memory-controller clock cycles.
Defaults roughly follow a DDR4-2400-like part (in cycles, not ns).
"""

from dataclasses import dataclass


def _is_power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


@dataclass
class DRAMConfig:
    """Organization + timing knobs for the simulated DRAM system.

    Organization counts must be powers of two so that address bits can be
    sliced cleanly by the address mapper.
    """

    # --- Organization ---
    num_channels: int = 1
    num_ranks: int = 1
    num_banks: int = 8
    num_rows: int = 4096
    num_columns: int = 1024  # column bits also cover the byte offset (MVP)

    # --- Timing (cycles) ---
    tRCD: int = 14  # ACTIVATE -> READ/WRITE delay
    tCL: int = 14   # READ/WRITE -> data delay (CAS latency; also used for WRITE)
    tRP: int = 14   # PRECHARGE -> ACTIVATE delay
    tRAS: int = 33  # ACTIVATE -> PRECHARGE minimum
    tRC: int = 47   # ACTIVATE -> ACTIVATE minimum (same bank), typically tRAS + tRP

    def __post_init__(self) -> None:
        for name in ("num_channels", "num_ranks", "num_banks", "num_rows", "num_columns"):
            if not _is_power_of_two(getattr(self, name)):
                raise ValueError(f"{name} must be a power of two, got {getattr(self, name)}")
        for name in ("tRCD", "tCL", "tRP", "tRAS", "tRC"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive, got {getattr(self, name)}")
        if self.tRC < self.tRAS:
            raise ValueError("tRC must be >= tRAS")

    @property
    def total_banks(self) -> int:
        """Total independently schedulable banks across the whole system."""
        return self.num_channels * self.num_ranks * self.num_banks
