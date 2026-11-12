"""Tests for Vornado Transom ModeController discovery."""

from __future__ import annotations

import json

from alexa_payloads import capabilities, oscillation_position_capability

from load_component import load

discover_vornado_controls = load("alexa.fan_controls").discover_vornado_controls


def test_discover_vornado_controls_maps_speed_and_direction() -> None:
    """Synthetic Transom capabilities discover expected bindings."""
    controls = discover_vornado_controls(capabilities())
    assert controls is not None
    assert controls.speed_instance == "1"
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
    assert controls.direction is not None
    assert controls.direction.instance == "2"
    assert controls.direction.direct_mode.alexa_mode == "Direction.Direct"
    assert controls.direction.direct_mode.label == "Direct"
    assert controls.direction.exhaust_mode.alexa_mode == "Direction.Exhaust"
    assert controls.direction.exhaust_mode.label == "Exhaust"


def test_discover_speed_only_fan_has_no_direction() -> None:
    """A 4-speed fan with no direction controller is still discovered."""
    controls = discover_vornado_controls(capabilities(include_direction=False))
    assert controls is not None
    assert controls.speed_instance == "1"
    assert [mode.label for mode in controls.speed_modes] == [
        "Low",
        "Medium",
        "High",
        "Turbo",
    ]
    assert controls.direction is None


def test_discover_eight_mode_position_controller_is_not_direction() -> None:
    """An 8-mode Direction/Position controller stays off airflow direction."""
    caps = capabilities(include_direction=False)
    caps.append(oscillation_position_capability())
    controls = discover_vornado_controls(caps)
    assert controls is not None
    assert controls.speed_instance == "1"
    assert [mode.label for mode in controls.speed_modes] == [
        "Low",
        "Medium",
        "High",
        "Turbo",
    ]
    assert controls.direction is None


def test_discover_rejects_three_mode_speed_controller() -> None:
    """A 3-mode speed controller is not a Transom match."""
    assert discover_vornado_controls(capabilities(speed_modes=3)) is None


def test_discover_accepts_capabilities_json_string() -> None:
    """legacyAppliance.capabilities may arrive as a JSON string."""
    controls = discover_vornado_controls(json.dumps(capabilities()))
    assert controls is not None
    assert controls.speed_modes[0].label == "Low"
    assert controls.direction is not None
    assert controls.direction.instance == "2"
