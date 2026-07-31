"""Tests for Vornado Transom ModeController discovery."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


def _load_fan_controls():
    """Load discovery helpers without importing Home Assistant package init."""
    base = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "vornado_transom"
    )
    alexa = base / "alexa"

    for name, path in (
        ("custom_components", base.parent),
        ("custom_components.vornado_transom", base),
        ("custom_components.vornado_transom.alexa", alexa),
    ):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            module.__package__ = name
            sys.modules[name] = module

    for full_name, file_path in (
        (
            "custom_components.vornado_transom.alexa.models",
            alexa / "models.py",
        ),
        (
            "custom_components.vornado_transom.alexa.fan_controls",
            alexa / "fan_controls.py",
        ),
    ):
        if full_name in sys.modules and hasattr(sys.modules[full_name], "__file__"):
            continue
        spec = importlib.util.spec_from_file_location(full_name, file_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = module
        spec.loader.exec_module(module)

    return sys.modules["custom_components.vornado_transom.alexa.fan_controls"]


fan_controls = _load_fan_controls()
discover_vornado_controls = fan_controls.discover_vornado_controls


def _mode(value: str, text: str) -> dict:
    return {
        "value": value,
        "modeResources": {
            "friendlyNames": [
                {"@type": "text", "value": {"text": text}},
            ]
        },
    }


def _speed_capability(*, mode_count: int = 4) -> dict:
    labels = ("Low", "Medium", "High", "Turbo")
    modes = [
        _mode(f"Speed.{labels[i]}", labels[i]) for i in range(mode_count)
    ]
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
            "supportedModes": modes,
        },
    }


def _direction_capability() -> dict:
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
                _mode("Direction.Direct", "Direct"),
                _mode("Direction.Exhaust", "Exhaust"),
            ],
        },
    }


def _capabilities(*, speed_modes: int = 4) -> list[dict]:
    return [
        {
            "interfaceName": "Alexa.PowerController",
            "properties": {"readOnly": False},
        },
        _speed_capability(mode_count=speed_modes),
        _direction_capability(),
    ]


def test_discover_vornado_controls_maps_speed_and_direction() -> None:
    """Synthetic Transom capabilities discover expected bindings."""
    controls = discover_vornado_controls(_capabilities())
    assert controls is not None
    assert controls.speed_instance == "1"
    assert controls.direction_instance == "2"
    assert [mode.label for mode in controls.speed_modes] == [
        "Low",
        "Medium",
        "High",
        "Turbo",
    ]
    assert [mode.alexa_mode for mode in controls.speed_modes] == [
        "Speed.Low",
        "Speed.Medium",
        "Speed.High",
        "Speed.Turbo",
    ]
    assert controls.direct_mode.alexa_mode == "Direction.Direct"
    assert controls.direct_mode.label == "Direct"
    assert controls.exhaust_mode.alexa_mode == "Direction.Exhaust"
    assert controls.exhaust_mode.label == "Exhaust"


def test_discover_rejects_three_mode_speed_controller() -> None:
    """A 3-mode speed controller is not a Transom match."""
    assert discover_vornado_controls(_capabilities(speed_modes=3)) is None


def test_discover_accepts_capabilities_json_string() -> None:
    """legacyAppliance.capabilities may arrive as a JSON string."""
    controls = discover_vornado_controls(json.dumps(_capabilities()))
    assert controls is not None
    assert controls.speed_modes[0].label == "Low"
