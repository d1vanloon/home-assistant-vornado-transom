"""Tests for climate and fan command planning."""

from __future__ import annotations

import asyncio
import math

import pytest

from load_component import load

commands = load("alexa.commands")
endpoints = load("alexa.endpoints")
execution = load("alexa.execution")
models = load("alexa.models")


def _device(
    *,
    power: str | None = "OFF",
    auto_on: bool | None = None,
    climate: bool = True,
    direction: bool = True,
):
    speed_modes = tuple(
        models.ModeBinding("1", str(index), label)
        for index, label in enumerate(("Low", "Medium", "High", "Turbo"), start=1)
    )
    auto_climate = None
    if climate:
        auto_climate = models.AutoClimateControls(
            "4",
            "3",
            models.TemperatureRange(60.0, 90.0, 1.0),
        )
    airflow = None
    if direction:
        airflow = models.DirectionControls(
            "2",
            models.ModeBinding("2", "2", "Direct"),
            models.ModeBinding("2", "1", "Exhaust"),
        )
    return models.FanDevice(
        endpoint_id="endpoint-1",
        appliance_key="abc123",
        name="Bedroom Transom",
        serial_number=None,
        model=None,
        manufacturer=None,
        controls=models.FanControlMap(
            speed_instance="1",
            speed_modes=speed_modes,
            direction=airflow,
            auto_climate=auto_climate,
        ),
        power=power,
        auto_on=auto_on,
    )


def test_fan_state_query_locks_toggle_state_fragment() -> None:
    """The toggle fragment type is ToggleState. A wrong type fails every poll."""
    queries = load("alexa.queries")
    query = queries.QUERY_FAN_STATE
    assert queries.TOGGLE_STATE_TYPE == "ToggleState"
    fragment = """
        ... on ToggleState {
          toggleStateValue
        }
"""
    assert fragment in query
    assert query.count("ToggleState") == 1
    assert "... on Power {" in query
    assert "... on Mode {" in query
    assert "... on RangeValue {" in query


def test_power_and_mode_requests_match_current_bodies() -> None:
    """Power and mode dicts stay the bodies async_set_power and async_set_mode sent."""
    assert commands.feature_request("endpoint-1", commands.PowerWrite(True)) == {
        "endpointId": "endpoint-1",
        "featureName": "power",
        "featureOperationName": "turnOn",
        "payload": {},
    }
    assert commands.feature_request("endpoint-1", commands.PowerWrite(False)) == {
        "endpointId": "endpoint-1",
        "featureName": "power",
        "featureOperationName": "turnOff",
        "payload": {},
    }
    assert commands.feature_request(
        "endpoint-1", commands.ModeWrite("1", "3")
    ) == {
        "endpointId": "endpoint-1",
        "featureName": "mode",
        "featureOperationName": "setMode",
        "instance": "1",
        "payload": {"mode": "3"},
    }


@pytest.mark.parametrize("auto_on", [False, None])
def test_climate_off_sends_nothing_when_auto_is_not_on(auto_on: bool | None) -> None:
    """Climate Off does not stop a fan that auto did not start."""
    device = _device(power="ON", auto_on=auto_on)
    assert commands.plan(device, commands.SetHvacAuto(False)) == ()


def test_climate_off_turns_toggle_off_then_power_off() -> None:
    device = _device(power="ON", auto_on=True)
    assert commands.plan(device, commands.SetHvacAuto(False)) == (
        commands.ToggleWrite("4", False),
        commands.PowerWrite(False),
    )


def test_climate_auto_turns_power_on_then_toggle_on() -> None:
    device = _device(power="ON", auto_on=True)
    assert commands.plan(device, commands.SetHvacAuto(True)) == (
        commands.PowerWrite(True),
        commands.ToggleWrite("4", True),
    )


def test_set_target_72_sends_power_toggle_then_range() -> None:
    device = _device()
    writes = commands.plan(device, commands.SetTargetTemperature(72))
    assert writes == (
        commands.PowerWrite(True),
        commands.ToggleWrite("4", True),
        commands.RangeWrite("3", 72.0),
    )
    assert commands.feature_request(device.endpoint_id, writes[2])["payload"] == {
        commands.RANGE_PAYLOAD_KEY: 72.0
    }
    assert commands.RANGE_PAYLOAD_KEY == "rangeValue"
    updated = endpoints.apply_fan_state(device, commands.expected_states(writes))
    assert (updated.power, updated.auto_on, updated.target_temperature) == (
        "ON",
        True,
        72.0,
    )


@pytest.mark.parametrize("value", [72.5, 59, math.nan])
def test_set_target_rejects_off_grid_and_out_of_range(value: float) -> None:
    with pytest.raises(commands.CommandRejected):
        commands.plan(_device(), commands.SetTargetTemperature(value))


def test_target_with_hvac_off_is_rejected_before_any_write() -> None:
    """A temperature request that also asks for off does not turn the fan on."""
    device = _device(power="OFF", auto_on=False)
    with pytest.raises(commands.CommandRejected, match="off"):
        commands.plan(device, commands.SetTargetTemperature(72, "off"))
    assert commands.plan(device, commands.SetTargetTemperature(72, "auto")) == (
        commands.PowerWrite(True),
        commands.ToggleWrite("4", True),
        commands.RangeWrite("3", 72.0),
    )


def test_set_target_requires_climate_controls() -> None:
    with pytest.raises(commands.CommandRejected):
        commands.plan(_device(climate=False), commands.SetTargetTemperature(72))


@pytest.mark.parametrize("auto_on", [False, None])
def test_climate_fan_mode_rejected_when_auto_is_not_on(auto_on: bool | None) -> None:
    device = _device(power="ON", auto_on=auto_on)
    with pytest.raises(commands.CommandRejected):
        commands.plan(device, commands.SetClimateFanMode("High"))


def test_climate_fan_mode_while_auto_is_on_uses_the_fan_preset_sequence() -> None:
    device = _device(power="OFF", auto_on=True)
    assert commands.plan(device, commands.SetClimateFanMode("High")) == (
        commands.PowerWrite(True),
        commands.ModeWrite("1", "3"),
    )
    already_on = _device(power="ON", auto_on=True)
    assert commands.plan(already_on, commands.SetClimateFanMode("High")) == (
        commands.ModeWrite("1", "3"),
    )
    with pytest.raises(commands.CommandRejected):
        commands.plan(already_on, commands.SetClimateFanMode("Blast"))


def test_fan_preset_while_off_turns_power_on_then_sets_speed() -> None:
    device = _device(power="OFF", auto_on=True)
    assert commands.plan(device, commands.SelectSpeed("High")) == (
        commands.PowerWrite(True),
        commands.ModeWrite("1", "3"),
    )
    already_on = _device(power="ON", auto_on=False)
    assert commands.plan(already_on, commands.SelectSpeed("Low")) == (
        commands.ModeWrite("1", "1"),
    )


def test_fan_power_writes_do_not_touch_auto() -> None:
    device = _device(power="ON", auto_on=True)
    assert commands.plan(device, commands.TurnOff()) == (commands.PowerWrite(False),)
    assert commands.plan(device, commands.TurnOn()) == (commands.PowerWrite(True),)


def test_empty_climate_off_does_not_publish() -> None:
    """An Off plan with nothing to send must not mark the fan available."""
    device = _device(auto_on=None)
    device.available = False
    published: list = []

    async def send(write: object) -> None:
        raise AssertionError(write)

    asyncio.run(
        execution.CommandGate().run(
            "abc123",
            lambda: device,
            commands.SetHvacAuto(False),
            send,
            published.append,
        )
    )
    assert published == []
    assert device.available is False


def test_failed_setpoint_keeps_the_power_and_auto_writes() -> None:
    """A failed range write still records the power and auto writes that succeeded."""
    current = _device(power="OFF", auto_on=False)
    published: list = []

    async def send(write: object) -> None:
        nonlocal current
        if isinstance(write, commands.PowerWrite):
            current = _device(power="OFF", auto_on=False)
            current.air_temperature = 71.0
        if isinstance(write, commands.RangeWrite):
            raise models.AlexaApiError("range failed")

    with pytest.raises(models.AlexaApiError, match="range failed"):
        asyncio.run(
            execution.CommandGate().run(
                "abc123",
                lambda: current,
                commands.SetTargetTemperature(72),
                send,
                published.append,
            )
        )
    assert len(published) == 1
    assert published[0].power == "ON"
    assert published[0].auto_on is True
    assert published[0].target_temperature is None
    assert published[0].air_temperature == 71.0


def test_a_failed_first_write_publishes_nothing() -> None:
    device = _device(power="OFF", auto_on=False)
    published: list = []

    async def send(write: object) -> None:
        raise models.AlexaApiError("down")

    with pytest.raises(models.AlexaApiError, match="down"):
        asyncio.run(
            execution.CommandGate().run(
                "abc123",
                lambda: device,
                commands.SetHvacAuto(True),
                send,
                published.append,
            )
        )
    assert published == []


def test_a_connection_error_keeps_writes_that_succeeded() -> None:
    """Power and auto remain published when the setpoint request cannot connect."""

    class CannotConnect(Exception):
        pass

    device = _device(power="OFF", auto_on=False)
    published: list = []

    async def send(write: object) -> None:
        if isinstance(write, commands.RangeWrite):
            raise CannotConnect("socket closed")

    with pytest.raises(CannotConnect, match="socket closed"):
        asyncio.run(
            execution.CommandGate().run(
                "abc123",
                lambda: device,
                commands.SetTargetTemperature(72),
                send,
                published.append,
                (CannotConnect,),
            )
        )
    assert published[0].power == "ON"
    assert published[0].auto_on is True
    assert published[0].target_temperature is None


def test_a_poll_started_before_a_command_keeps_the_command() -> None:
    """An in-flight poll must not publish the snapshot it took before the command."""

    async def scenario() -> None:
        gate = execution.CommandGate()
        device = _device(power="OFF", auto_on=False)
        started = {"abc123": gate.epoch("abc123")}

        def publish(updated) -> None:
            nonlocal device
            device = updated

        async def send(write: object) -> None:
            return None

        await gate.run(
            "abc123",
            lambda: device,
            commands.SetHvacAuto(True),
            send,
            publish,
        )
        stale = _device(power="OFF", auto_on=None)
        merged = await gate.merge_poll(started, {"abc123": stale}, lambda key: device)
        assert merged["abc123"].auto_on is True
        assert merged["abc123"].power == "ON"

        fresh = _device(power="ON", auto_on=True)
        fresh.air_temperature = 70.0
        current = await gate.merge_poll(
            {"abc123": gate.epoch("abc123")},
            {"abc123": fresh},
            lambda key: device,
        )
        assert current["abc123"].air_temperature == 70.0

    asyncio.run(scenario())


def test_cancellation_publishes_writes_that_finished() -> None:
    """Cancelling a later write keeps the earlier ones and blocks a stale poll."""

    async def scenario() -> None:
        gate = execution.CommandGate()
        device = _device(power="OFF", auto_on=False)
        started = {"abc123": gate.epoch("abc123")}
        published: list = []

        async def send(write: object) -> None:
            if isinstance(write, commands.RangeWrite):
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await gate.run(
                "abc123",
                lambda: device,
                commands.SetTargetTemperature(72),
                send,
                published.append,
            )
        assert published[0].power == "ON"
        assert published[0].auto_on is True
        stale = _device(power="OFF", auto_on=None)
        merged = await gate.merge_poll(
            started, {"abc123": stale}, lambda key: published[0]
        )
        assert merged["abc123"].auto_on is True
        assert merged["abc123"].power == "ON"

    asyncio.run(scenario())


def test_commands_for_one_fan_run_one_at_a_time() -> None:
    """Off waits until Auto publishes, so it does not plan against the old state."""

    async def scenario() -> None:
        lane = execution.CommandGate()
        device = _device(power="OFF", auto_on=False)
        sent: list = []
        first_write = asyncio.Event()

        def publish(updated) -> None:
            nonlocal device
            device = updated

        async def send(write: object) -> None:
            sent.append(write)
            if len(sent) == 1:
                first_write.set()
                await asyncio.sleep(0)

        async def turn_auto_on() -> None:
            await lane.run(
                "abc123",
                lambda: device,
                commands.SetHvacAuto(True),
                send,
                publish,
            )

        async def turn_auto_off() -> None:
            await first_write.wait()
            await lane.run(
                "abc123",
                lambda: device,
                commands.SetHvacAuto(False),
                send,
                publish,
            )

        await asyncio.gather(turn_auto_on(), turn_auto_off())
        assert [type(write) for write in sent] == [
            commands.PowerWrite,
            commands.ToggleWrite,
            commands.ToggleWrite,
            commands.PowerWrite,
        ]
        assert sent[2].on is False
        assert sent[3].on is False
        assert device.auto_on is False
        assert device.power == "OFF"

    asyncio.run(scenario())


def test_direction_maps_forward_to_direct_and_reverse_to_exhaust() -> None:
    device = _device()
    assert commands.plan(device, commands.SelectDirection(False)) == (
        commands.ModeWrite("2", "2"),
    )
    assert commands.plan(device, commands.SelectDirection(True)) == (
        commands.ModeWrite("2", "1"),
    )
    with pytest.raises(commands.CommandRejected):
        commands.plan(_device(direction=False), commands.SelectDirection(False))
