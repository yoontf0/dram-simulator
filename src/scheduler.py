"""Memory-controller scheduling policies.

A scheduler only picks WHICH pending request to service next; all timing
is computed by the bank model. This keeps policies tiny and swappable.

FCFS     : always the oldest request.
FR-FCFS  : First-Ready FCFS — among pending requests, prefer the oldest
           one that would be a row-buffer HIT; fall back to the oldest.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Mapping, Sequence

from src.dram_bank import AccessType, Bank

if TYPE_CHECKING:  # avoid a runtime circular import with memory_controller
    from src.memory_controller import Request

BankKey = tuple[int, int, int]


class Scheduler(ABC):
    """Picks the index of the next request to service from the pending queue.

    `pending` is always ordered oldest-first (arrival order).
    """

    name: str

    @abstractmethod
    def select(self, pending: Sequence["Request"], banks: Mapping[BankKey, Bank]) -> int:
        ...


class FCFSScheduler(Scheduler):
    """First-Come First-Served: strictly in arrival order."""

    name = "FCFS"

    def select(self, pending: Sequence["Request"], banks: Mapping[BankKey, Bank]) -> int:
        return 0


class FRFCFSScheduler(Scheduler):
    """First-Ready FCFS: row-buffer hits first, then oldest."""

    name = "FR-FCFS"

    def select(self, pending: Sequence["Request"], banks: Mapping[BankKey, Bank]) -> int:
        for i, req in enumerate(pending):
            bank = banks[req.location.bank_key]
            if bank.classify(req.location.row) is AccessType.HIT:
                return i
        return 0  # no hit available -> oldest request


def make_scheduler(policy: str) -> Scheduler:
    """Factory: 'fcfs' or 'frfcfs' (case-insensitive, dashes ignored)."""
    key = policy.lower().replace("-", "").replace("_", "")
    if key == "fcfs":
        return FCFSScheduler()
    if key == "frfcfs":
        return FRFCFSScheduler()
    raise ValueError(f"unknown scheduling policy: {policy!r} (use 'FCFS' or 'FR-FCFS')")
