"""Tests for climate and fan command planning."""

from __future__ import annotations

import math

import pytest

from load_component import load

commands = load("alexa.commands")
endpoints = load("alexa.endpoints")
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
