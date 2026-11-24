"""Intents, the writes they produce, and the Nexus request for each write."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .models import (
    CapabilityState,
    ClimatePhase,
    FanDevice,
    TemperatureRange,
    climate_phase,
)

# Inferred. SetRangeValue's payload key matches the polled rangeValue property;
# this mutation was not captured on a Transom.
RANGE_PAYLOAD_KEY = "rangeValue"


@dataclass(frozen=True)
class TurnOn:
    """Turn fan power on. Does not change the auto toggle."""


@dataclass(frozen=True)
class TurnOff:
    """Turn fan power off. Clears auto when it is on, so climate leaves cool."""


@dataclass(frozen=True)
class SelectSpeed:
    """Select a discovered speed label. Turns power on when it is not already on."""

    label: str


@dataclass(frozen=True)
class SelectDirection:
    """Set airflow direction. False is forward (direct); True is reverse (exhaust)."""

    reverse: bool


@dataclass(frozen=True)
class SetHvacCool:
    """Turn power on and enable auto."""


@dataclass(frozen=True)
class SetHvacOff:
    """Turn power off, and turn auto off when it is on."""


@dataclass(frozen=True)
class SetHvacFanOnly:
    """Turn auto off and leave the fan powered on."""


@dataclass(frozen=True)
class SetClimateFanMode:
    """Select a speed label while cool or fan only is active."""

    label: str


@dataclass(frozen=True)
class SetTargetTemperature:
    """Set the auto-mode target temperature.

    hvac_mode is set when the climate service sent one. None and cool keep the
    temperature-only behavior, which turns auto on. Any other mode is rejected
    before a write.
    """

    value: float
    hvac_mode: str | None = None


type Intent = (
    TurnOn
    | TurnOff
    | SelectSpeed
    | SelectDirection
    | SetHvacCool
    | SetHvacOff
    | SetHvacFanOnly
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


class Step(StrEnum):
    """One absolute write, in the order a policy lists it.

    AUTO_OFF_UNLESS_FALSE also sends when auto is unknown.
    AUTO_OFF_ONLY_IF_TRUE does not.
    POWER_ON and AUTO_ON always send. They do not look at the current value.
    """

    POWER_ON = "power_on"
    POWER_OFF = "power_off"
    POWER_ON_UNLESS_ON = "power_on_unless_on"
    POWER_OFF_UNLESS_OFF = "power_off_unless_off"
    AUTO_ON = "auto_on"
    AUTO_OFF_UNLESS_FALSE = "auto_off_unless_false"
    AUTO_OFF_ONLY_IF_TRUE = "auto_off_only_if_true"
    SPEED = "speed"
    DIRECTION = "direction"
    SETPOINT = "setpoint"


@dataclass(frozen=True)
class WritePolicy:
    """Gates run before steps. Step order is send order."""

    steps: tuple[Step, ...]
    requires_climate: bool = False
    requires_direction: bool = False
    phases: frozenset[ClimatePhase] | None = None
    target_modes: frozenset[str | None] | None = None


_ACTIVE = frozenset({ClimatePhase.COOL, ClimatePhase.FAN_ONLY})
_COOL_TARGET = frozenset({None, ClimatePhase.COOL})

_POLICY: dict[type[Intent], WritePolicy] = {
    TurnOn: WritePolicy((Step.POWER_ON,)),
    TurnOff: WritePolicy((Step.AUTO_OFF_ONLY_IF_TRUE, Step.POWER_OFF)),
    SelectSpeed: WritePolicy((Step.POWER_ON_UNLESS_ON, Step.SPEED)),
    SelectDirection: WritePolicy((Step.DIRECTION,), requires_direction=True),
    SetHvacCool: WritePolicy(
        (Step.POWER_ON, Step.AUTO_ON),
        requires_climate=True,
    ),
    SetHvacOff: WritePolicy(
        (Step.AUTO_OFF_ONLY_IF_TRUE, Step.POWER_OFF_UNLESS_OFF),
        requires_climate=True,
    ),
    SetHvacFanOnly: WritePolicy(
        (Step.AUTO_OFF_UNLESS_FALSE, Step.POWER_ON_UNLESS_ON),
        requires_climate=True,
    ),
    SetClimateFanMode: WritePolicy(
        (Step.POWER_ON_UNLESS_ON, Step.SPEED),
        phases=_ACTIVE,
    ),
    SetTargetTemperature: WritePolicy(
        (Step.POWER_ON, Step.AUTO_ON, Step.SETPOINT),
        requires_climate=True,
        target_modes=_COOL_TARGET,
    ),
}


class CommandRejected(Exception):
    """The intent is invalid for this device. Nothing was sent."""


def climate_intent(hvac_mode: str) -> Intent:
    """Return the climate intent for one HVAC mode string."""
    if hvac_mode == ClimatePhase.COOL:
        return SetHvacCool()
    if hvac_mode == ClimatePhase.FAN_ONLY:
        return SetHvacFanOnly()
    if hvac_mode == ClimatePhase.OFF:
        return SetHvacOff()
    raise CommandRejected(f"Unsupported HVAC mode: {hvac_mode}")


def plan(device: FanDevice, intent: Intent) -> tuple[FeatureWrite, ...]:
    """Return the absolute writes for one intent, in send order."""
    policy = _POLICY.get(type(intent))
    if policy is None:
        raise CommandRejected(f"Unknown intent: {intent!r}")
    _reject_unless_allowed(device, intent, policy)
    return _assemble(device, intent, policy)


def _reject_unless_allowed(
    device: FanDevice, intent: Intent, policy: WritePolicy
) -> None:
    if policy.target_modes is not None and isinstance(intent, SetTargetTemperature):
        if intent.hvac_mode not in policy.target_modes:
            raise CommandRejected(
                "Target temperature cannot be set while HVAC mode is "
                f"{intent.hvac_mode}"
            )
    if policy.phases is not None and climate_phase(device) not in policy.phases:
        raise CommandRejected("Climate is off")
    if policy.requires_climate and device.controls.auto_climate is None:
        raise CommandRejected("This fan has no auto climate")
    if policy.requires_direction and device.controls.direction is None:
        raise CommandRejected("This fan does not support airflow direction")


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


def _assemble(
    device: FanDevice, intent: Intent, policy: WritePolicy
) -> tuple[FeatureWrite, ...]:
    writes: list[FeatureWrite] = []
    for step in policy.steps:
        write = _step_write(device, intent, step)
        if write is not None:
            writes.append(write)
    return tuple(writes)


def _step_write(
    device: FanDevice, intent: Intent, step: Step
) -> FeatureWrite | None:
    climate = device.controls.auto_climate
    match step:
        case Step.POWER_ON:
            return PowerWrite(True)
        case Step.POWER_OFF:
            return PowerWrite(False)
        case Step.POWER_ON_UNLESS_ON if device.power != "ON":
            return PowerWrite(True)
        case Step.POWER_OFF_UNLESS_OFF if device.power != "OFF":
            return PowerWrite(False)
        case Step.AUTO_ON:
            if climate is None:
                raise CommandRejected("This fan has no auto climate")
            return ToggleWrite(climate.toggle_instance, True)
        case Step.AUTO_OFF_UNLESS_FALSE if device.auto_on is not False:
            if climate is None:
                raise CommandRejected("This fan has no auto climate")
            return ToggleWrite(climate.toggle_instance, False)
        case Step.AUTO_OFF_ONLY_IF_TRUE if (
            device.auto_on is True and climate is not None
        ):
            return ToggleWrite(climate.toggle_instance, False)
        case Step.SPEED if isinstance(intent, (SelectSpeed, SetClimateFanMode)):
            binding = next(
                (
                    mode
                    for mode in device.controls.speed_modes
                    if mode.label == intent.label
                ),
                None,
            )
            if binding is None:
                raise CommandRejected(f"Invalid preset mode: {intent.label}")
            return ModeWrite(binding.instance, binding.alexa_mode)
        case Step.DIRECTION if isinstance(intent, SelectDirection):
            direction = device.controls.direction
            if direction is None:
                raise CommandRejected("This fan does not support airflow direction")
            binding = (
                direction.exhaust_mode if intent.reverse else direction.direct_mode
            )
            return ModeWrite(binding.instance, binding.alexa_mode)
        case Step.SETPOINT if isinstance(intent, SetTargetTemperature):
            if climate is None:
                raise CommandRejected("This fan has no auto climate")
            number = _setpoint(intent.value, climate.set_temperature_range)
            if number is None:
                raise CommandRejected(f"Invalid target temperature: {intent.value}")
            return RangeWrite(climate.set_temperature_instance, number)
        case _:
            return None


def quantize_setpoint(value: float, span: TemperatureRange) -> float | None:
    """Nearest in-range grid point for a climate target.

    Home Assistant converts Celsius to Fahrenheit without rounding, so 22°C
    arrives as 71.6. A value already on the grid is unchanged. A value outside
    the range, or exactly halfway between steps, stays invalid.
    """
    exact = _setpoint(value, span)
    if exact is not None:
        return exact
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if not (
        math.isfinite(span.minimum)
        and math.isfinite(span.maximum)
        and math.isfinite(span.precision)
        and span.precision > 0
    ):
        return None
    if number < span.minimum or number > span.maximum:
        return None
    steps = (number - span.minimum) / span.precision
    nearest = round(steps)
    if abs(steps - nearest) >= 0.5 - 1e-9:
        return None
    return _setpoint(span.minimum + nearest * span.precision, span)


def _setpoint(value: float, span: TemperatureRange) -> float | None:
    """Return value when it sits on the discovered grid, otherwise None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if not (
        math.isfinite(span.minimum)
        and math.isfinite(span.maximum)
        and math.isfinite(span.precision)
        and span.precision > 0
    ):
        return None
    if number < span.minimum or number > span.maximum:
        return None
    steps = (number - span.minimum) / span.precision
    if abs(steps - round(steps)) > 1e-6:
        return None
    return number
