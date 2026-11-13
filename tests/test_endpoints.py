"""Tests for endpoint admission, state folding, and the entity contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from alexa_payloads import capabilities, endpoint, mode, speed_capability

from load_component import load

endpoints = load("alexa.endpoints")
models = load("alexa.models")
Admitted = endpoints.Admitted
Gate = endpoints.Gate
Rejected = endpoints.Rejected
CapabilityState = models.CapabilityState


def _gate(item: dict) -> Gate | None:
    verdict = endpoints.classify_endpoint(item)
    return verdict.gate if isinstance(verdict, Rejected) else None


def test_transom_endpoint_is_admitted() -> None:
    verdict = endpoints.classify_endpoint(endpoint("abc123"))
    assert isinstance(verdict, Admitted)
    device = verdict.device
    assert device.appliance_key == "abc123"
    assert device.endpoint_id == "amzn1.alexa.endpoint.abc123"
    assert device.name == "Bedroom Transom"
    assert (device.serial_number, device.model, device.manufacturer) == (
        "VT123",
        "Transom",
        "Vornado",
    )


def test_missing_enablement_passes() -> None:
    item = endpoint()
    del item["enablement"]
    assert isinstance(endpoints.classify_endpoint(item), Admitted)


@pytest.mark.parametrize(
    ("item", "gate"),
    [
        (endpoint(enablement="DISABLED"), Gate.ENABLEMENT),
        (endpoint(category="LIGHT"), Gate.CATEGORY),
        (endpoint(legacyAppliance={"applianceKey": "abc123"}), Gate.CAPABILITIES),
        (
            endpoint(legacyAppliance={"applianceKey": "abc123", "capabilities": "not json"}),
            Gate.CAPABILITIES,
        ),
        (endpoint(caps=capabilities(speed_modes=3)), Gate.CONTROLS),
        (endpoint(caps=[]), Gate.CONTROLS),
        (
            endpoint(id=None, legacyAppliance={"capabilities": capabilities()}),
            Gate.IDENTITY,
        ),
    ],
    ids=["enablement", "category", "no-capabilities", "bad-json", "three-speeds", "empty-list", "identity"],
)
def test_each_gate_rejects(item: dict, gate: Gate) -> None:
    assert _gate(item) is gate


def test_first_failing_gate_wins() -> None:
    assert _gate(endpoint(enablement="DISABLED", category="LIGHT", caps=[])) is Gate.ENABLEMENT


def test_appliance_key_falls_back_to_endpoint_id_without_prefix() -> None:
    item = endpoint(legacyAppliance={"capabilities": capabilities()})
    verdict = endpoints.classify_endpoint(item)
    assert isinstance(verdict, Admitted)
    assert verdict.device.appliance_key == "abc123"


def test_mode_without_value_is_rejected() -> None:
    speed = speed_capability()
    speed["configuration"]["supportedModes"][0] = mode("ignored", "Low")
    del speed["configuration"]["supportedModes"][0]["value"]
    verdict = endpoints.classify_endpoint(endpoint(caps=[speed]))
    assert isinstance(verdict, Rejected)
    assert verdict.gate is Gate.CONTROLS
    assert verdict.detail == "speed ModeController has a mode with no value"


def test_matching_direction_without_a_value_is_rejected() -> None:
    caps = capabilities()
    direction = caps[-1]
    direction["configuration"]["supportedModes"][0] = mode("ignored", "Direct")
    del direction["configuration"]["supportedModes"][0]["value"]
    verdict = endpoints.classify_endpoint(endpoint(caps=caps))
    assert isinstance(verdict, Rejected)
    assert verdict.gate is Gate.CONTROLS
    assert verdict.detail == "matching direction controller has a mode with no value"


def test_last_duplicate_appliance_key_is_kept() -> None:
    first = endpoints.classify_endpoint(endpoint("abc123", name="First"))
    second = endpoints.classify_endpoint(endpoint("abc123", name="Second"))
    kept = endpoints.kept_devices([first, Rejected(Gate.CATEGORY, "x"), second])
    assert list(kept) == ["abc123"]
    assert kept["abc123"].name == "Second"


def _template():
    verdict = endpoints.classify_endpoint(endpoint())
    assert isinstance(verdict, Admitted)
    return verdict.device


def test_apply_fan_state_reads_power_speed_and_direction() -> None:
    device = endpoints.apply_fan_state(
        _template(),
        [
            CapabilityState(name="power", power_state="ON"),
            CapabilityState(name="mode", instance="1", mode_value="Speed.High"),
            CapabilityState(name="mode", instance="2", mode_value="Direction.Exhaust"),
            CapabilityState(name="range", instance="5", range_value=72.0),
            CapabilityState(name="toggle", instance="9", power_state="OFF"),
        ],
    )
    assert (device.power, device.speed_mode, device.direction_mode) == (
        "ON",
        "Speed.High",
        "Direction.Exhaust",
    )
    assert device.air_temperature is None
    assert device.available is True


def test_apply_fan_state_keeps_previous_power_when_new_value_is_none() -> None:
    previous = endpoints.apply_fan_state(
        _template(), [CapabilityState(name="power", power_state="ON")]
    )
    device = endpoints.apply_fan_state(
        previous, [CapabilityState(name="power", power_state=None)]
    )
    assert device.power == "ON"


def test_apply_fan_state_ignores_direction_without_a_direction_controller() -> None:
    verdict = endpoints.classify_endpoint(endpoint(caps=capabilities(include_direction=False)))
    assert isinstance(verdict, Admitted)
    device = endpoints.apply_fan_state(
        verdict.device,
        [CapabilityState(name="mode", instance="2", mode_value="Direction.Exhaust")],
    )
    assert device.direction_mode is None


def test_entity_contract_falls_back_to_vornado_transom() -> None:
    template = _template()
    template.manufacturer = None
    template.model = None
    contract = endpoints.entity_contract(template)
    assert contract.manufacturer == "Vornado"
    assert contract.model == "Transom"
    assert contract.unique_id == "abc123_fan"
    assert contract.preset_modes == ("Low", "Medium", "High", "Turbo")
    assert contract.direction_modes == endpoints.DirectionModes(
        "Direction.Direct", "Direction.Exhaust"
    )


def test_recorded_transom_matches_live_bindings() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "transom_alexa.json").read_text(encoding="utf-8")
    )
    verdict = endpoints.classify_endpoint(fixture["endpoint"])
    assert isinstance(verdict, Admitted)
    controls = verdict.device.controls
    assert [binding.alexa_mode for binding in controls.speed_modes] == ["1", "2", "3", "4"]
    assert [binding.label for binding in controls.speed_modes] == [
        "Low",
        "Medium",
        "High",
        "Turbo",
    ]
    assert controls.direction is not None
    assert controls.direction.instance == "2"
    assert controls.direction.direct_mode.alexa_mode == "2"
    assert controls.direction.direct_mode.label == "Direct"
    assert controls.direction.exhaust_mode.alexa_mode == "1"
    assert controls.direction.exhaust_mode.label == "Exhaust"
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
    device = endpoints.apply_fan_state(
        verdict.device,
        [
            CapabilityState(
                name=feature["name"],
                instance=feature["instance"],
                power_state=feature["power_state"],
                mode_value=feature["mode_value"],
                range_value=feature["range_value"],
            )
            for feature in fixture["fan_state"]["features"]
        ],
    )
    assert device.power == "OFF"
    assert device.speed_mode is None
    assert device.direction_mode is None
    assert device.target_temperature is None
    assert device.air_temperature == 72.0
    assert device.auto_on is None


def _recorded():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "transom_alexa.json").read_text(
            encoding="utf-8"
        )
    )
    verdict = endpoints.classify_endpoint(fixture["endpoint"])
    assert isinstance(verdict, Admitted)
    return verdict.device


def test_apply_fan_state_folds_auto_setpoint_and_sentinels() -> None:
    """Toggle and in-range setpoints stick. 0 and unknown modes clear the field."""
    device = _recorded()
    reading = endpoints.apply_fan_state(
        device,
        [
            CapabilityState(name="toggle", instance="4", toggle_enabled=True),
            CapabilityState(name="range", instance="3", range_value=72.0),
            CapabilityState(name="range", instance="5", range_value=0.0),
            CapabilityState(name="mode", instance="1", mode_value="3"),
            CapabilityState(name="mode", instance="2", mode_value="2"),
        ],
    )
    assert reading.auto_on is True
    assert reading.target_temperature == 72.0
    assert reading.air_temperature == 0.0
    assert reading.speed_mode == "3"
    assert reading.direction_mode == "2"

    kept = endpoints.apply_fan_state(
        reading,
        [
            CapabilityState(name="toggle", instance="4"),
            CapabilityState(name="range", instance="3"),
        ],
    )
    assert kept.auto_on is True
    assert kept.target_temperature == 72.0

    cleared = endpoints.apply_fan_state(
        reading,
        [
            CapabilityState(name="toggle", instance="4", toggle_enabled=False),
            CapabilityState(name="range", instance="3", range_value=0.0),
            CapabilityState(name="range", instance="3", range_value=59.0),
            CapabilityState(name="mode", instance="1", mode_value="0"),
            CapabilityState(name="mode", instance="2", mode_value="0"),
        ],
    )
    assert cleared.auto_on is False
    assert cleared.target_temperature is None
    assert cleared.speed_mode is None
    assert cleared.direction_mode is None
    assert cleared.air_temperature == 0.0


def test_climate_card_has_a_target_when_the_device_setpoint_is_unset() -> None:
    """The climate card draws a setpoint only when temperature is a number."""
    device = endpoints.apply_fan_state(
        _recorded(),
        [CapabilityState(name="range", instance="3", range_value=0.0)],
    )
    assert device.target_temperature is None
    assert endpoints.climate_target_temperature(device) == 60.0


def test_climate_card_uses_a_reported_setpoint() -> None:
    device = endpoints.apply_fan_state(
        _recorded(),
        [CapabilityState(name="range", instance="3", range_value=72.0)],
    )
    assert endpoints.climate_target_temperature(device) == 72.0


def test_climate_swing_mode_is_forward_or_reverse() -> None:
    direct = endpoints.apply_fan_state(
        _recorded(),
        [CapabilityState(name="mode", instance="2", mode_value="2")],
    )
    exhaust = endpoints.apply_fan_state(
        _recorded(),
        [CapabilityState(name="mode", instance="2", mode_value="1")],
    )
    assert endpoints.climate_swing_mode(direct) == "forward"
    assert endpoints.climate_swing_mode(exhaust) == "reverse"
    assert endpoints.climate_swing_mode(_recorded()) is None


def test_synthetic_fan_without_climate_controllers_has_no_auto_climate() -> None:
    """Climate features on a speed-only fan do not create auto state."""
    device = _template()
    assert device.controls.auto_climate is None
    folded = endpoints.apply_fan_state(
        device,
        [
            CapabilityState(name="toggle", instance="4", toggle_enabled=True),
            CapabilityState(name="range", instance="3", range_value=72.0),
        ],
    )
    assert folded.auto_on is None
    assert folded.target_temperature is None
