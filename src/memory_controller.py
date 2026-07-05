"""Memory controller: request queue -> scheduler -> banks.

Simplified event loop (inspired by DRAMSim2's transaction/command queues):
  1. Requests whose arrival cycle has passed enter the pending queue.
  2. The scheduling policy picks one pending request.
  3. The target bank computes the command sequence and data-ready cycle,
     honoring its own timing constraints (tRCD/tCL/tRP/tRAS/tRC).
  4. The controller clock advances one cycle per serviced request, so
     requests to DIFFERENT banks may overlap (bank-level parallelism),
     while each bank serializes its own commands.

MVP simplifications: no REFRESH, no tCCD/tBURST, no data-bus contention.
"""

from dataclasses import dataclass, field

from src.address_mapper import AddressMapper, Location
from src.dram_bank import AccessType, Bank, Command
from src.dram_config import DRAMConfig
from src.scheduler import Scheduler


@dataclass(frozen=True)
class Request:
    """One memory request from the input trace."""

    req_id: int
    arrival_cycle: int
    address: int
    is_write: bool
    location: Location


@dataclass(frozen=True)
class RequestResult:
    """Outcome of one serviced request."""

    request: Request
    access_type: AccessType
    commands: list[Command] = field(compare=False)
    start_cycle: int   # cycle the first command was issued
    done_cycle: int    # cycle the data is available
    latency: int       # done_cycle - arrival_cycle


class MemoryController:
    """Drives all banks of the system under one scheduling policy."""

    def __init__(self, config: DRAMConfig, scheduler: Scheduler) -> None:
        self.config = config
        self.scheduler = scheduler
        self.mapper = AddressMapper(config)
        self.banks: dict[tuple[int, int, int], Bank] = {
            (ch, rk, bk): Bank(config, (ch, rk, bk))
            for ch in range(config.num_channels)
            for rk in range(config.num_ranks)
            for bk in range(config.num_banks)
        }

    def make_request(self, req_id: int, arrival_cycle: int, address: int, is_write: bool) -> Request:
        """Decode the address once and attach the location to the request."""
        return Request(
            req_id=req_id,
            arrival_cycle=arrival_cycle,
            address=address,
            is_write=is_write,
            location=self.mapper.decompose(address),
        )

    def run(self, requests: list[Request]) -> list[RequestResult]:
        """Service every request; returns results in service order."""
        queue = sorted(requests, key=lambda r: (r.arrival_cycle, r.req_id))
        pending: list[Request] = []
        results: list[RequestResult] = []
        current_cycle = 0
        next_arrival = 0  # index into `queue`

        while next_arrival < len(queue) or pending:
            # Admit every request that has arrived by now (keeps arrival order).
            while next_arrival < len(queue) and queue[next_arrival].arrival_cycle <= current_cycle:
                pending.append(queue[next_arrival])
                next_arrival += 1

            if not pending:
                # Nothing to do: fast-forward to the next arrival.
                current_cycle = queue[next_arrival].arrival_cycle
                continue

            chosen = pending.pop(self.scheduler.select(pending, self.banks))
            bank = self.banks[chosen.location.bank_key]
            access_type, commands, done = bank.access(
                row=chosen.location.row,
                column=chosen.location.column,
                is_write=chosen.is_write,
                cycle=current_cycle,
            )
            results.append(
                RequestResult(
                    request=chosen,
                    access_type=access_type,
                    commands=commands,
                    start_cycle=commands[0].cycle,
                    done_cycle=done,
                    latency=done - chosen.arrival_cycle,
                )
            )
            # One scheduling slot per request; bank timing handles the rest.
            current_cycle += 1

        return results
