"""Physical address decomposition into Channel/Rank/Bank/Row/Column.

Bit layout (LSB -> MSB): column | channel | bank | rank | row

Putting the row bits at the top means consecutive addresses stay in the
same row (good spatial locality -> row-buffer hits), while the bank bits
below the row bits let different rows spread across banks (bank-level
parallelism). This mirrors common "row interleaving" schemes discussed in
DRAMSim2 / Ramulator.
"""

from dataclasses import dataclass

from src.dram_config import DRAMConfig


@dataclass(frozen=True)
class Location:
    """A fully decoded DRAM coordinate for one physical address."""

    channel: int
    rank: int
    bank: int
    row: int
    column: int

    @property
    def bank_key(self) -> tuple[int, int, int]:
        """Unique key identifying the physical bank (channel, rank, bank)."""
        return (self.channel, self.rank, self.bank)


class AddressMapper:
    """Slices a flat physical address into DRAM coordinates."""

    def __init__(self, config: DRAMConfig) -> None:
        self.config = config
        self._col_bits = (config.num_columns - 1).bit_length()
        self._ch_bits = (config.num_channels - 1).bit_length()
        self._bank_bits = (config.num_banks - 1).bit_length()
        self._rank_bits = (config.num_ranks - 1).bit_length()
        self._row_bits = (config.num_rows - 1).bit_length()

        # Shift amounts for each field, LSB -> MSB.
        self._ch_shift = self._col_bits
        self._bank_shift = self._ch_shift + self._ch_bits
        self._rank_shift = self._bank_shift + self._bank_bits
        self._row_shift = self._rank_shift + self._rank_bits

    def decompose(self, address: int) -> Location:
        """Decode a physical address into (channel, rank, bank, row, column)."""
        if address < 0:
            raise ValueError(f"address must be non-negative, got {address}")
        cfg = self.config
        return Location(
            column=address & (cfg.num_columns - 1),
            channel=(address >> self._ch_shift) & (cfg.num_channels - 1),
            bank=(address >> self._bank_shift) & (cfg.num_banks - 1),
            rank=(address >> self._rank_shift) & (cfg.num_ranks - 1),
            # Extra high bits beyond num_rows simply wrap around (MVP choice).
            row=(address >> self._row_shift) & (cfg.num_rows - 1),
        )

    def compose(self, channel: int, rank: int, bank: int, row: int, column: int) -> int:
        """Inverse of decompose(). Handy for tests and trace generation."""
        return (
            column
            | (channel << self._ch_shift)
            | (bank << self._bank_shift)
            | (rank << self._rank_shift)
            | (row << self._row_shift)
        )
