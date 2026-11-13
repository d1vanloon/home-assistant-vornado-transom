"""Tests for Vornado Transom ModeController discovery."""

from __future__ import annotations

import json

from alexa_payloads import (
    air_temperature_capability,
    auto_toggle_capability,
    capabilities,
    oscillation_position_capability,
    set_temperature_capability,
)

from load_component import load

discover_vornado_controls = load("alexa.fan_controls").discover_vornado_controls


def test_discover_vornado_controls_maps_speed_and_direction() -> None:
    """Synthetic Transom capabilities discover expected bindings."""
    controls, _detail = discover_vornado_controls(capabilities())
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
    assert controls.auto_climate is None


def test_discover_speed_only_fan_has_no_direction() -> None:
    """A 4-speed fan with no direction controller is still discovered."""
    controls, _detail = discover_vornado_controls(capabilities(include_direction=False))
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
    controls, _detail = discover_vornado_controls(caps)
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
    controls, detail = discover_vornado_controls(capabilities(speed_modes=3))
    assert controls is None
    assert detail == "no writable, ordered, 4-mode speed ModeController"


def test_discover_binds_auto_climate_when_both_controllers_exist() -> None:
    """A writable auto toggle and set-temperature range bind together."""
    controls, detail = discover_vornado_controls(
        capabilities()
        + [
            air_temperature_capability(),
            set_temperature_capability(),
            auto_toggle_capability(name="Autoclimate"),
        ]
    )
    assert detail == ""
    assert controls is not None
    assert controls.air_temperature_instance == "5"
    climate = controls.auto_climate
    assert climate is not None
    assert climate.toggle_instance == "4"
    assert climate.set_temperature_instance == "3"
    assert (
        climate.set_temperature_range.minimum,
        climate.set_temperature_range.maximum,
        climate.set_temperature_range.precision,
    ) == (60.0, 90.0, 1.0)
    assert [mode.label for mode in controls.speed_modes] == [
        "Low",
        "Medium",
        "High",
        "Turbo",
    ]


def test_air_temperature_is_not_the_setpoint() -> None:
    """A range named Air Temperature stays the sensor, even when it is writable."""
    controls, detail = discover_vornado_controls(
        capabilities()
        + [
            air_temperature_capability(read_only=False),
            auto_toggle_capability(),
        ]
    )
    assert detail == ""
    assert controls is not None
    assert controls.air_temperature_instance == "5"
    assert controls.auto_climate is None


def test_incomplete_climate_controls_keep_the_fan() -> None:
    """A missing or malformed climate controller does not reject the fan."""
    bad_range = set_temperature_capability()
    del bad_range["configuration"]["supportedRange"]["precision"]
    cases = [
        [auto_toggle_capability()],
        [set_temperature_capability()],
        [
            auto_toggle_capability(),
            set_temperature_capability(minimum=90.0, maximum=60.0),
        ],
        [auto_toggle_capability(), set_temperature_capability(precision=0.0)],
        [auto_toggle_capability(), set_temperature_capability(minimum=float("-inf"))],
        [auto_toggle_capability(), set_temperature_capability(maximum=float("inf"))],
        [auto_toggle_capability(), set_temperature_capability(precision=float("nan"))],
        [auto_toggle_capability(), bad_range],
        [auto_toggle_capability(read_only=True), set_temperature_capability()],
        [auto_toggle_capability(), set_temperature_capability(read_only=True)],
    ]
    for extra in cases:
        controls, detail = discover_vornado_controls(capabilities() + extra)
        assert detail == ""
        assert controls is not None
        assert controls.auto_climate is None
        assert [mode.label for mode in controls.speed_modes] == [
            "Low",
            "Medium",
            "High",
            "Turbo",
        ]


def test_discover_accepts_capabilities_json_string() -> None:
    """legacyAppliance.capabilities may arrive as a JSON string."""
    controls, _detail = discover_vornado_controls(json.dumps(capabilities()))
    assert controls is not None
    assert controls.speed_modes[0].label == "Low"
    assert controls.direction is not None
    assert controls.direction.instance == "2"
