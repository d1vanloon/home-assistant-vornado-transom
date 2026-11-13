"""Intents, the writes they produce, and the Nexus request for each write."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .models import (
    AutoClimateControls,
    CapabilityState,
    FanDevice,
    TemperatureRange,
)

# Inferred. SetRangeValue's payload key matches the polled rangeValue property;
# this mutation was not captured on a Transom.
RANGE_PAYLOAD_KEY = "rangeValue"


@dataclass(frozen=True)
class TurnOn:
    """Turn fan power on. Does not change the auto toggle."""


@dataclass(frozen=True)
class TurnOff:
    """Turn fan power off. Does not change the auto toggle."""


@dataclass(frozen=True)
class SelectSpeed:
    """Select a discovered speed label. Turns power on when it is not already on."""

    label: str


@dataclass(frozen=True)
class SelectDirection:
    """Set airflow direction. False is forward (direct); True is reverse (exhaust)."""

    reverse: bool


@dataclass(frozen=True)
class SetHvacAuto:
    """Turn the device's auto mode on or off."""

    on: bool


@dataclass(frozen=True)
class SetClimateFanMode:
    """Select a speed label while auto is already on."""

    label: str


@dataclass(frozen=True)
class SetTargetTemperature:
    """Set the auto-mode target temperature."""

    value: float


type Intent = (
    TurnOn
    | TurnOff
    | SelectSpeed
    | SelectDirection
    | SetHvacAuto
    | SetClimateFanMode
    | SetTargetTemperature
)


@dataclass(frozen=True)
class PowerWrite:
    """Absolute power. No instance."""

    on: bool


@dataclass(frozen=True)
class ModeWrite:
    """Absolute ModeController value."""

    instance: str
    mode: str


@dataclass(frozen=True)
class ToggleWrite:
    """Absolute ToggleController value."""

    instance: str
    on: bool


@dataclass(frozen=True)
class RangeWrite:
    """Absolute RangeController value. Not snapped."""

    instance: str
    value: float


type FeatureWrite = PowerWrite | ModeWrite | ToggleWrite | RangeWrite


class CommandRejected(Exception):
    """The intent is invalid for this device. Nothing was sent."""


def plan(device: FanDevice, intent: Intent) -> tuple[FeatureWrite, ...]:
    """Return the absolute writes for one intent, in send order."""
    match intent:
        case TurnOn():
            return (PowerWrite(True),)
        case TurnOff():
            return (PowerWrite(False),)
        case SelectSpeed(label):
            return _speed_writes(device, label)
        case SelectDirection(reverse):
            direction = device.controls.direction
            if direction is None:
                raise CommandRejected("This fan does not support airflow direction")
            binding = direction.exhaust_mode if reverse else direction.direct_mode
            return (ModeWrite(binding.instance, binding.alexa_mode),)
        case SetHvacAuto(on):
            return _auto_writes(device, on)
        case SetClimateFanMode(label):
            if device.auto_on is not True:
                raise CommandRejected("Auto climate is off")
            return _speed_writes(device, label)
        case SetTargetTemperature(value):
            return _target_writes(device, value)
    raise CommandRejected(f"Unknown intent: {intent!r}")


def expected_states(writes: Iterable[FeatureWrite]) -> list[CapabilityState]:
    """Each write as the polled feature apply_fan_state should fold."""
    states: list[CapabilityState] = []
    for write in writes:
        match write:
            case PowerWrite(on):
                states.append(
                    CapabilityState(name="power", power_state="ON" if on else "OFF")
                )
            case ModeWrite(instance, mode):
                states.append(
                    CapabilityState(name="mode", instance=instance, mode_value=mode)
                )
            case ToggleWrite(instance, on):
                states.append(
                    CapabilityState(
                        name="toggle", instance=instance, toggle_enabled=on
                    )
                )
            case RangeWrite(instance, value):
                states.append(
                    CapabilityState(name="range", instance=instance, range_value=value)
                )
    return states


def feature_request(endpoint_id: str, write: FeatureWrite) -> dict[str, Any]:
    """Build one setEndpointFeatures request. The only Nexus request builder."""
    match write:
        case PowerWrite(on):
            return {
                "endpointId": endpoint_id,
                "featureName": "power",
                "featureOperationName": "turnOn" if on else "turnOff",
                "payload": {},
            }
        case ModeWrite(instance, mode):
            return {
                "endpointId": endpoint_id,
                "featureName": "mode",
                "featureOperationName": "setMode",
                "instance": instance,
                "payload": {"mode": mode},
            }
        case ToggleWrite(instance, on):
            return {
                "endpointId": endpoint_id,
                "featureName": "toggle",
                "featureOperationName": "turnOn" if on else "turnOff",
                "instance": instance,
                "payload": {},
            }
        case RangeWrite(instance, value):
            return {
                "endpointId": endpoint_id,
                "featureName": "range",
                "featureOperationName": "setRangeValue",
                "instance": instance,
                "payload": {RANGE_PAYLOAD_KEY: value},
            }
    raise TypeError(f"Unknown write: {write!r}")


def _speed_writes(device: FanDevice, label: str) -> tuple[FeatureWrite, ...]:
    binding = next(
        (mode for mode in device.controls.speed_modes if mode.label == label),
        None,
    )
    if binding is None:
        raise CommandRejected(f"Invalid preset mode: {label}")
    writes: list[FeatureWrite] = []
    if device.power != "ON":
        writes.append(PowerWrite(True))
    writes.append(ModeWrite(binding.instance, binding.alexa_mode))
    return tuple(writes)


def _auto_writes(device: FanDevice, on: bool) -> tuple[FeatureWrite, ...]:
    if not on and device.auto_on is not True:
        return ()
    climate = _require_climate(device)
    if on:
        return (PowerWrite(True), ToggleWrite(climate.toggle_instance, True))
    return (ToggleWrite(climate.toggle_instance, False), PowerWrite(False))


def _target_writes(device: FanDevice, value: float) -> tuple[FeatureWrite, ...]:
    climate = _require_climate(device)
    number = _setpoint(value, climate.set_temperature_range)
    if number is None:
        raise CommandRejected(f"Invalid target temperature: {value}")
    return (
        PowerWrite(True),
        ToggleWrite(climate.toggle_instance, True),
        RangeWrite(climate.set_temperature_instance, number),
    )


def _require_climate(device: FanDevice) -> AutoClimateControls:
    climate = device.controls.auto_climate
    if climate is None:
        raise CommandRejected("This fan has no auto climate")
    return climate


def _setpoint(value: float, span: TemperatureRange) -> float | None:
    """Return value when it sits on the discovered grid, otherwise None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number < span.minimum or number > span.maximum:
        return None
    steps = (number - span.minimum) / span.precision
    if abs(steps - round(steps)) > 1e-6:
        return None
    return number
