"""The WiNet-S retry wrapper."""

from __future__ import annotations

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusTimeoutError,
    ModbusUnit,
    ServerDeviceBusyError,
    ServerDeviceFailureError,
)
from modbus_connection.mock import MockModbusUnit

from sungrow_inverter import RetryingUnit, RetryPolicy, SungrowInverter
from sungrow_inverter.components import Settings

from .conftest import FlakyUnit


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


async def test_retries_device_failure_then_succeeds(unit: MockModbusUnit) -> None:
    flaky = FlakyUnit(unit, ServerDeviceFailureError(), failures=2)
    sleeps = Sleeps()
    retrying = RetryingUnit(flaky, sleep=sleeps)
    assert await retrying.read_holding_registers(13017, 1) == [0x55]
    assert flaky.calls == 3
    assert retrying.retries == 2
    assert sleeps.delays == [0.25, 0.5]


async def test_gives_up_after_the_last_attempt(unit: MockModbusUnit) -> None:
    flaky = FlakyUnit(unit, ServerDeviceBusyError(), failures=3)
    retrying = RetryingUnit(flaky, RetryPolicy(attempts=3), sleep=Sleeps())
    with pytest.raises(ServerDeviceBusyError):
        await retrying.read_input_registers(12999, 2)
    assert flaky.calls == 3


async def test_never_retries_a_real_refusal(unit: MockModbusUnit) -> None:
    flaky = FlakyUnit(unit, IllegalDataAddressError(), failures=1)
    sleeps = Sleeps()
    retrying = RetryingUnit(flaky, sleep=sleeps)
    with pytest.raises(IllegalDataAddressError):
        await retrying.read_input_registers(13249, 15)
    assert flaky.calls == 1
    assert sleeps.delays == []


async def test_never_retries_a_connection_loss(unit: MockModbusUnit) -> None:
    flaky = FlakyUnit(unit, ModbusConnectionError(), failures=1)
    retrying = RetryingUnit(flaky, sleep=Sleeps())
    with pytest.raises(ModbusConnectionError):
        await retrying.read_input_registers(12999, 2)
    assert flaky.calls == 1


async def test_timeouts_retry_only_when_the_policy_says(
    unit: MockModbusUnit,
) -> None:
    flaky = FlakyUnit(unit, ModbusTimeoutError(), failures=1)
    retrying = RetryingUnit(flaky, sleep=Sleeps())
    assert await retrying.read_holding_registers(33046, 2) == [1200, 1200]

    flaky = FlakyUnit(unit, ModbusTimeoutError(), failures=1)
    strict = RetryingUnit(flaky, RetryPolicy(retry_timeouts=False), sleep=Sleeps())
    with pytest.raises(ModbusTimeoutError):
        await strict.read_holding_registers(33046, 2)


async def test_single_attempt_policy_never_retries(unit: MockModbusUnit) -> None:
    flaky = FlakyUnit(unit, ServerDeviceFailureError(), failures=1)
    retrying = RetryingUnit(flaky, RetryPolicy(attempts=1), sleep=Sleeps())
    with pytest.raises(ServerDeviceFailureError):
        await retrying.read_holding_registers(13017, 1)
    assert retrying.retries == 0


async def test_writes_are_retried_too(unit: MockModbusUnit) -> None:
    flaky = FlakyUnit(unit, ServerDeviceFailureError(), failures=1)
    retrying = RetryingUnit(flaky, sleep=Sleeps())
    await retrying.write_register(13017, 0xAA)
    assert unit.holding[13017] == 0xAA
    assert retrying.retries == 1


async def test_a_flaky_block_does_not_fail_the_component(
    unit: MockModbusUnit,
) -> None:
    flaky = FlakyUnit(unit, ServerDeviceFailureError(), failures=1)
    retrying = RetryingUnit(flaky, sleep=Sleeps())
    settings = Settings(retrying)
    await settings.async_update()
    assert settings.export_limit == 15000
    assert retrying.retries == 1


async def test_inverter_reads_through_the_wrapper(unit: MockModbusUnit) -> None:
    flaky = FlakyUnit(unit, ServerDeviceFailureError(), failures=3)
    retrying = RetryingUnit(flaky, RetryPolicy(attempts=2), sleep=Sleeps())
    inverter = SungrowInverter(retrying)
    # three failures at two attempts a read: the first setup exhausts both,
    # the second setup's identity read fails once and its retry gets through
    with pytest.raises(ServerDeviceFailureError):
        await inverter.async_update()
    report = await inverter.async_update()
    assert report.ok
    assert inverter.modbus_unit is retrying


def test_policy_delays_double() -> None:
    policy = RetryPolicy(base_delay=0.1)
    assert [policy.delay(n) for n in (1, 2, 3)] == [0.1, 0.2, 0.4]
    assert policy.should_retry(ServerDeviceFailureError())
    assert not policy.should_retry(IllegalDataAddressError())
    assert policy.should_retry(ModbusTimeoutError())
    assert not policy.should_retry(ModbusConnectionError())


async def test_wrapper_implements_the_whole_unit_protocol(
    unit: MockModbusUnit,
) -> None:
    retrying = RetryingUnit(unit)
    assert isinstance(retrying, ModbusUnit)
    assert retrying.wrapped is unit
    assert retrying.connected is unit.connected

    # the pass-through operations reach the unit untouched
    unit.set_response("read_exception_status", 0x11)
    assert await retrying.read_exception_status() == 0x11
    unit.set_response("report_server_id", b"\x01SH")
    assert await retrying.report_server_id() == b"\x01SH"
    unit.set_response("read_fifo_queue", [1, 2])
    assert await retrying.read_fifo_queue(0) == [1, 2]
    unit.set_response("read_device_identification", {0: b"Sungrow"})
    assert await retrying.read_device_identification() == {0: b"Sungrow"}
    unit.set_response("read_file_record", [7])
    assert await retrying.read_file_record(1, 2, 1) == [7]
    await retrying.write_file_record(1, 2, [7])
    unit.set_response("diagnostics", 5)
    assert await retrying.diagnostics(0) == 5
    unit.set_response("get_comm_event_counter", (True, 3))
    assert await retrying.get_comm_event_counter() == (True, 3)
    unit.set_response("get_comm_event_log", b"log")
    assert await retrying.get_comm_event_log() == b"log"

    unit.holding[13017] = 0x55
    await retrying.mask_write_register(13017, 0, 0xAA)
    assert unit.holding[13017] == 0xAA
    assert await retrying.read_write_registers(13017, 1, 13017, [0x55]) == [0x55]

    unit.coils[3] = True
    assert await retrying.read_coils(3, 1) == [True]
    assert await retrying.read_discrete_inputs(3, 1) == [False]
    await retrying.write_coil(4, True)
    await retrying.write_coils(5, [True, False])
    assert unit.coils[4] is True and unit.coils[5] is True

    retrying.set_message_spacing(0.1)
    assert unit.message_spacing == 0.1
    fired: list[str] = []
    unsubscribe = retrying.on_connection_lost(lambda: fired.append("lost"))
    await retrying.disconnect()
    assert fired == []  # disconnect() drops the link without firing
    unsubscribe()
