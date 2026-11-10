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

    controls = discover_vornado_controls(capabilities)
    if controls is None:
        return Rejected(
            Gate.CONTROLS,
            "capability list is empty"
            if not capabilities
            else "no writable, ordered, 4-mode speed ModeController",
        )

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
    return FeatureRole.IGNORED


def apply_fan_state(device: FanDevice, states: Iterable[CapabilityState]) -> FanDevice:
    """Fold polled features into a new available FanDevice. A None value keeps the old one."""
    power = device.power
    speed_mode = device.speed_mode
    direction_mode = device.direction_mode
    for state in states:
        match feature_role(device.controls, state):
            case FeatureRole.POWER if state.power_state is not None:
                power = state.power_state
            case FeatureRole.SPEED if state.mode_value is not None:
                speed_mode = state.mode_value
            case FeatureRole.DIRECTION if state.mode_value is not None:
                direction_mode = state.mode_value
    return replace(
        device,
        power=power,
        speed_mode=speed_mode,
        direction_mode=direction_mode,
        available=True,
    )


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
