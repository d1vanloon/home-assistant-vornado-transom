"""Which Alexa endpoints become fans, and how polled features fold into them."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from ..const import MANUFACTURER
from .fan_controls import discover_vornado_controls, parse_capabilities
from .models import CapabilityState, FanControlMap, FanDevice

ENDPOINT_PREFIX = "amzn1.alexa.endpoint."
DEFAULT_MODEL = "Transom"


class Gate(StrEnum):
    """Why an endpoint is not a fan, in evaluation order."""

    ENABLEMENT = "enablement"
    CATEGORY = "category"
    CAPABILITIES = "capabilities"
    CONTROLS = "controls"
    IDENTITY = "identity"


@dataclass(frozen=True)
class Admitted:
    """An endpoint that becomes a fan. Polled state is at its defaults."""

    device: FanDevice


@dataclass(frozen=True)
class Rejected:
    """The first gate an endpoint failed."""

    gate: Gate
    detail: str


def classify_endpoint(endpoint: Mapping[str, Any]) -> Admitted | Rejected:
    """Apply the admission gates to one raw endpoint. The first failure wins."""
    enablement = endpoint.get("enablement")
    if enablement is not None and enablement != "ENABLED":
        return Rejected(Gate.ENABLEMENT, f"enablement is {enablement!r}")

    display = endpoint.get("displayCategories")
    primary = display.get("primary") if isinstance(display, dict) else None
    primary_value = primary.get("value") if isinstance(primary, dict) else None
    if primary_value != "FAN":
        return Rejected(Gate.CATEGORY, f"primary display category is {primary_value!r}")

    legacy = endpoint.get("legacyAppliance")
    if not isinstance(legacy, dict):
        legacy = {}
    capabilities = parse_capabilities(legacy.get("capabilities"))
    if capabilities is None:
        return Rejected(
            Gate.CAPABILITIES, "legacyAppliance.capabilities is missing or not a JSON list"
        )

    controls, detail = discover_vornado_controls(capabilities)
    if controls is None:
        return Rejected(Gate.CONTROLS, detail)

    appliance_key = _appliance_key(endpoint)
    endpoint_id = _endpoint_id(endpoint)
    if not appliance_key or not endpoint_id:
        return Rejected(Gate.IDENTITY, "empty appliance key or endpoint id")

    return Admitted(
        FanDevice(
            endpoint_id=endpoint_id,
            appliance_key=appliance_key,
            name=str(endpoint.get("friendlyName") or appliance_key),
            serial_number=text_value(endpoint.get("serialNumber")),
            model=text_value(endpoint.get("model")),
            manufacturer=text_value(endpoint.get("manufacturer")),
            controls=controls,
        )
    )


def kept_devices(verdicts: Iterable[Admitted | Rejected]) -> dict[str, FanDevice]:
    """Admitted devices by appliance key. A later duplicate key replaces the earlier one."""
    return {
        verdict.device.appliance_key: verdict.device
        for verdict in verdicts
        if isinstance(verdict, Admitted)
    }


def text_value(field: Any) -> str | None:
    """Read a Nexus `{value: {text}}` wrapper."""
    if not isinstance(field, dict):
        return None
    value = field.get("value")
    if isinstance(value, dict):
        text = value.get("text")
        return str(text) if text is not None else None
    return None


def _appliance_key(endpoint: Mapping[str, Any]) -> str:
    legacy = endpoint.get("legacyAppliance") or {}
    if key := legacy.get("applianceKey"):
        return str(key)
    return _endpoint_id(endpoint).removeprefix(ENDPOINT_PREFIX)


def _endpoint_id(endpoint: Mapping[str, Any]) -> str:
    return str(endpoint.get("id") or endpoint.get("endpointId") or "")


class FeatureRole(StrEnum):
    """Which fan binding a polled feature feeds."""

    POWER = "power"
    SPEED = "speed"
    DIRECTION = "direction"
    AIR_TEMPERATURE = "air_temperature"
    SET_TEMPERATURE = "set_temperature"
    AUTO = "auto"
    IGNORED = "ignored"


def feature_role(controls: FanControlMap, state: CapabilityState) -> FeatureRole:
    """Attribute one polled feature by name and instance."""
    if state.name == "power":
        return FeatureRole.POWER
    if state.name == "mode":
        if state.instance == controls.speed_instance:
            return FeatureRole.SPEED
        if controls.direction is not None and state.instance == controls.direction.instance:
            return FeatureRole.DIRECTION
    if (
        state.name == "range"
        and controls.air_temperature_instance is not None
        and state.instance == controls.air_temperature_instance
    ):
        return FeatureRole.AIR_TEMPERATURE
    climate = controls.auto_climate
    if (
        climate is not None
        and state.name == "range"
        and state.instance == climate.set_temperature_instance
    ):
        return FeatureRole.SET_TEMPERATURE
    if (
        climate is not None
        and state.name == "toggle"
        and state.instance == climate.toggle_instance
    ):
        return FeatureRole.AUTO
    return FeatureRole.IGNORED


def apply_fan_state(device: FanDevice, states: Iterable[CapabilityState]) -> FanDevice:
    """Fold polled features into a new available FanDevice.

    A None value keeps the previous field. A reported speed, direction, or set
    temperature outside the discovered domain becomes None. Air temperature is
    kept as reported, including 0.
    """
    power = device.power
    speed_mode = device.speed_mode
    direction_mode = device.direction_mode
    air_temperature = device.air_temperature
    auto_on = device.auto_on
    target_temperature = device.target_temperature
    speed_modes = {mode.alexa_mode for mode in device.controls.speed_modes}
    direction = device.controls.direction
    direction_modes = (
        set()
        if direction is None
        else {direction.direct_mode.alexa_mode, direction.exhaust_mode.alexa_mode}
    )
    climate = device.controls.auto_climate
    span = None if climate is None else climate.set_temperature_range
    for state in states:
        match feature_role(device.controls, state):
            case FeatureRole.POWER if state.power_state is not None:
                power = state.power_state
            case FeatureRole.SPEED if state.mode_value is not None:
                speed_mode = state.mode_value if state.mode_value in speed_modes else None
            case FeatureRole.DIRECTION if state.mode_value is not None:
                direction_mode = (
                    state.mode_value if state.mode_value in direction_modes else None
                )
            case FeatureRole.AIR_TEMPERATURE if state.range_value is not None:
                air_temperature = state.range_value
            case FeatureRole.AUTO if state.toggle_enabled is not None:
                auto_on = state.toggle_enabled
            case FeatureRole.SET_TEMPERATURE if state.range_value is not None:
                value = state.range_value
                target_temperature = (
                    value
                    if span is not None and span.minimum <= value <= span.maximum
                    else None
                )
    return replace(
        device,
        power=power,
        speed_mode=speed_mode,
        direction_mode=direction_mode,
        air_temperature=air_temperature,
        auto_on=auto_on,
        target_temperature=target_temperature,
        available=True,
    )


def mark_unavailable(device: FanDevice) -> FanDevice:
    """Keep the last reading and mark the fan unavailable."""
    return replace(device, available=False)


def fold_poll(
    cached: FanDevice | None,
    template: FanDevice,
    states: Iterable[CapabilityState],
) -> FanDevice:
    """Fold a poll onto the last published device, or the template on the first poll."""
    return apply_fan_state(template if cached is None else cached, states)


CLIMATE_FORWARD = "forward"
CLIMATE_REVERSE = "reverse"


def climate_target_temperature(device: FanDevice) -> float | None:
    """Temperature the climate card can render.

    Home Assistant draws the setpoint control only when the temperature
    attribute is a number. The device reports 0 for no setpoint, and the fold
    stores that as None, so the card falls back to the discovered minimum.
    """
    climate = device.controls.auto_climate
    if climate is None:
        return None
    if device.target_temperature is not None:
        return device.target_temperature
    return climate.set_temperature_range.minimum


def climate_swing_mode(device: FanDevice) -> str | None:
    """Forward or reverse for the climate card, from the direction controller."""
    direction = device.controls.direction
    if direction is None or not device.direction_mode:
        return None
    if device.direction_mode == direction.direct_mode.alexa_mode:
        return CLIMATE_FORWARD
    if device.direction_mode == direction.exhaust_mode.alexa_mode:
        return CLIMATE_REVERSE
    return None


@dataclass(frozen=True)
class DirectionModes:
    """Alexa mode values for forward and reverse airflow."""

    direct: str
    exhaust: str


@dataclass(frozen=True)
class EntityContract:
    """What Home Assistant shows for a kept fan, before any polling."""

    unique_id: str
    manufacturer: str
    model: str
    preset_modes: tuple[str, ...]
    direction_modes: DirectionModes | None


def entity_contract(device: FanDevice) -> EntityContract:
    """Project a FanDevice onto the Home Assistant entity and device facts."""
    direction = device.controls.direction
    return EntityContract(
        unique_id=f"{device.appliance_key}_fan",
        manufacturer=device.manufacturer or MANUFACTURER,
        model=device.model or DEFAULT_MODEL,
        preset_modes=tuple(mode.label for mode in device.controls.speed_modes),
        direction_modes=(
            None
            if direction is None
            else DirectionModes(
                direction.direct_mode.alexa_mode,
                direction.exhaust_mode.alexa_mode,
            )
        ),
    )
