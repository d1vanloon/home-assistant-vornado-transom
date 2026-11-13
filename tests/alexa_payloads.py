"""Synthetic Alexa Nexus payloads shaped like a Vornado Transom."""

from __future__ import annotations

import json
from typing import Any


def mode(value: str, text: str) -> dict:
    return {
        "value": value,
        "modeResources": {
            "friendlyNames": [
                {"@type": "text", "value": {"text": text}},
            ]
        },
    }


def speed_capability(*, mode_count: int = 4) -> dict:
    labels = ("Low", "Medium", "High", "Turbo")
    return {
        "interfaceName": "Alexa.ModeController",
        "instance": "1",
        "properties": {"readOnly": False},
        "resources": {
            "friendlyNames": [
                {"@type": "text", "value": {"text": "Fan Speed"}},
            ]
        },
        "configuration": {
            "ordered": True,
            "supportedModes": [
                mode(f"Speed.{labels[i]}", labels[i]) for i in range(mode_count)
            ],
        },
    }


def direction_capability() -> dict:
    return {
        "interfaceName": "Alexa.ModeController",
        "instance": "2",
        "properties": {"readOnly": False},
        "resources": {
            "friendlyNames": [
                {"@type": "text", "value": {"text": "Direction"}},
            ]
        },
        "configuration": {
            "ordered": True,
            "supportedModes": [
                mode("Direction.Direct", "Direct"),
                mode("Direction.Exhaust", "Exhaust"),
            ],
        },
    }


def oscillation_position_capability(*, mode_count: int = 8) -> dict:
    return {
        "interfaceName": "Alexa.ModeController",
        "instance": "3",
        "properties": {"readOnly": False},
        "resources": {
            "friendlyNames": [
                {"@type": "text", "value": {"text": "Direction"}},
                {"@type": "text", "value": {"text": "Position"}},
            ]
        },
        "configuration": {
            "ordered": True,
            "supportedModes": [
                mode(f"Position.{index}", f"Position {index}")
                for index in range(1, mode_count + 1)
            ],
        },
    }


def auto_toggle_capability(
    name: str = "Auto", *, instance: str = "4", read_only: bool = False
) -> dict:
    return {
        "interfaceName": "Alexa.ToggleController",
        "instance": instance,
        "properties": {"readOnly": read_only},
        "resources": {
            "friendlyNames": [
                {"@type": "text", "value": {"text": name}},
            ]
        },
    }


def range_capability(
    *,
    instance: str,
    name: str,
    minimum: float,
    maximum: float,
    precision: float,
    read_only: bool = False,
) -> dict:
    return {
        "interfaceName": "Alexa.RangeController",
        "instance": instance,
        "properties": {"readOnly": read_only},
        "resources": {
            "friendlyNames": [
                {"@type": "text", "value": {"text": name}},
            ]
        },
        "configuration": {
            "supportedRange": {
                "minimumValue": minimum,
                "maximumValue": maximum,
                "precision": precision,
            }
        },
    }


def set_temperature_capability(**overrides: Any) -> dict:
    values: dict[str, Any] = {
        "instance": "3",
        "name": "Set Temperature",
        "minimum": 60.0,
        "maximum": 90.0,
        "precision": 1.0,
        "read_only": False,
    }
    values.update(overrides)
    return range_capability(**values)


def air_temperature_capability(*, read_only: bool = True) -> dict:
    return range_capability(
        instance="5",
        name="Air Temperature",
        minimum=0.0,
        maximum=100.0,
        precision=1.0,
        read_only=read_only,
    )


def capabilities(
    *,
    speed_modes: int = 4,
    include_direction: bool = True,
    include_auto_climate: bool = False,
) -> list[dict]:
    result = [
        {
            "interfaceName": "Alexa.PowerController",
            "properties": {"readOnly": False},
        },
        speed_capability(mode_count=speed_modes),
    ]
    if include_direction:
        result.append(direction_capability())
    if include_auto_climate:
        result.append(set_temperature_capability())
        result.append(auto_toggle_capability())
    return result


def endpoint(
    key: str = "abc123",
    *,
    name: str = "Bedroom Transom",
    category: str = "FAN",
    caps: Any = None,
    **overrides: Any,
) -> dict:
    """One `endpoints.items` entry. `caps` is sent as a JSON string, as Alexa does."""
    item = {
        "id": f"amzn1.alexa.endpoint.{key}",
        "friendlyName": name,
        "displayCategories": {"primary": {"value": category}},
        "enablement": "ENABLED",
        "legacyAppliance": {
            "applianceKey": key,
            "capabilities": json.dumps(capabilities() if caps is None else caps),
        },
        "serialNumber": {"value": {"text": "VT123"}},
        "model": {"value": {"text": "Transom"}},
        "manufacturer": {"value": {"text": "Vornado"}},
        "features": [
            {"name": "power", "operations": [{"name": "turnOn"}, {"name": "turnOff"}]},
            {"name": "mode", "operations": [{"name": "setMode"}]},
        ],
    }
    item.update(overrides)
    return item
