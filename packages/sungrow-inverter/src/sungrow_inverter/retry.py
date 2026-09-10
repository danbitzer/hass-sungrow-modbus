"""A ``ModbusUnit`` wrapper that retries the transient failures a WiNet-S throws.

A WiNet-S dongle answers Modbus exception 4 (server device failure) now and
then when another client is polling it — contention, not a refusal. The
component planner must still see real refusals (codes 1-3), so only the
transient codes and, optionally, timeouts are retried.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from modbus_connection import ModbusExceptionError, ModbusTimeoutError, ModbusUnit

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to retry, which failures, and how long to wait."""

    attempts: int = 3
    """Total tries: one attempt plus ``attempts - 1`` retries."""
    base_delay: float = 0.25
    """Seconds before the first retry; doubles on each further one."""
    retry_codes: frozenset[int] = frozenset({4, 6, 10, 11})
    """Modbus exception codes treated as transient: device failure, busy,
    gateway path unavailable, gateway target failed to respond."""
    retry_timeouts: bool = True

    def delay(self, attempt: int) -> float:
        """Seconds to wait after the ``attempt``-th failed try (1-based)."""
        return float(self.base_delay * 2 ** (attempt - 1))

    def should_retry(self, error: BaseException) -> bool:
        """Whether ``error`` is a transient failure under this policy."""
        if isinstance(error, ModbusExceptionError):
            return error.exception_code in self.retry_codes
        if isinstance(error, ModbusTimeoutError):
            return self.retry_timeouts
        return False


class RetryingUnit:
    """Wrap a ``ModbusUnit`` so each operation retries transient failures.

    Implements the full ``ModbusUnit`` API, so a component reads through it
    unchanged. A retry re-issues the same request: register writes carry
    the same value, so a retried write is idempotent.
    """

    def __init__(
        self,
        unit: ModbusUnit,
        policy: RetryPolicy | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._unit = unit
        self._policy = policy if policy is not None else RetryPolicy()
        self._sleep = sleep
        self.retries = 0
        """Retries issued so far, for diagnostics."""

    @property
    def policy(self) -> RetryPolicy:
        return self._policy

    @property
    def wrapped(self) -> ModbusUnit:
        """The unit the requests go to."""
        return self._unit

    async def _call[T](
        self, op: Callable[..., Awaitable[T]], *args: object, label: str
    ) -> T:
        attempts = self._policy.attempts
        for attempt in range(1, attempts + 1):
            try:
                return await op(*args)
            except (ModbusExceptionError, ModbusTimeoutError) as err:
                if attempt == attempts or not self._policy.should_retry(err):
                    raise
                delay = self._policy.delay(attempt)
                _LOGGER.debug(
                    "%s failed (%s); retry %d/%d in %.2f s",
                    label,
                    err,
                    attempt,
                    attempts - 1,
                    delay,
                )
                self.retries += 1
                await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    @property
    def connected(self) -> bool:
        return self._unit.connected

    # -- register I/O --------------------------------------------------------

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        return await self._call(
            self._unit.read_holding_registers,
            address,
            count,
            label=f"read_holding_registers({address}, {count})",
        )

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        return await self._call(
            self._unit.read_input_registers,
            address,
            count,
            label=f"read_input_registers({address}, {count})",
        )

    async def write_register(self, address: int, value: int) -> None:
        await self._call(
            self._unit.write_register,
            address,
            value,
            label=f"write_register({address}, {value})",
        )

    async def write_registers(self, address: int, values: list[int]) -> None:
        await self._call(
            self._unit.write_registers,
            address,
            values,
            label=f"write_registers({address}, {len(values)} values)",
        )

    # -- coil / discrete-input I/O -------------------------------------------

    async def read_coils(self, address: int, count: int) -> list[bool]:
        return await self._call(
            self._unit.read_coils, address, count, label=f"read_coils({address})"
        )

    async def read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        return await self._call(
            self._unit.read_discrete_inputs,
            address,
            count,
            label=f"read_discrete_inputs({address})",
        )

    async def write_coil(self, address: int, value: bool) -> None:
        await self._call(
            self._unit.write_coil, address, value, label=f"write_coil({address})"
        )

    async def write_coils(self, address: int, values: list[bool]) -> None:
        await self._call(
            self._unit.write_coils, address, values, label=f"write_coils({address})"
        )

    # -- other function codes: passed through, not retried -------------------

    async def read_exception_status(self) -> int:
        return await self._unit.read_exception_status()

    async def report_server_id(self) -> bytes:
        return await self._unit.report_server_id()

    async def mask_write_register(
        self, address: int, and_mask: int, or_mask: int
    ) -> None:
        await self._unit.mask_write_register(address, and_mask, or_mask)

    async def read_write_registers(
        self,
        read_address: int,
        read_count: int,
        write_address: int,
        write_values: list[int],
    ) -> list[int]:
        return await self._unit.read_write_registers(
            read_address, read_count, write_address, write_values
        )

    async def read_fifo_queue(self, address: int) -> list[int]:
        return await self._unit.read_fifo_queue(address)

    async def read_device_identification(self) -> dict[int, bytes]:
        return await self._unit.read_device_identification()

    async def read_file_record(self, file: int, record: int, length: int) -> list[int]:
        return await self._unit.read_file_record(file, record, length)

    async def write_file_record(
        self, file: int, record: int, values: list[int]
    ) -> None:
        await self._unit.write_file_record(file, record, values)

    async def diagnostics(self, sub_function: int, data: int = 0) -> int:
        return await self._unit.diagnostics(sub_function, data)

    async def get_comm_event_counter(self) -> tuple[bool, int]:
        return await self._unit.get_comm_event_counter()

    async def get_comm_event_log(self) -> bytes:
        return await self._unit.get_comm_event_log()

    def set_message_spacing(self, seconds: float) -> None:
        self._unit.set_message_spacing(seconds)

    def on_connection_lost(self, callback: Callable[[], None]) -> Callable[[], None]:
        return self._unit.on_connection_lost(callback)

    async def disconnect(self) -> None:
        await self._unit.disconnect()
