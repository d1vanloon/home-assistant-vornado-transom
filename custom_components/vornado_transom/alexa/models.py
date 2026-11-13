"""Data models for Alexa Smart Home fan control."""

from __future__ import annotations

from dataclasses import dataclass


class AlexaApiError(Exception):
    """Raised when an Alexa GraphQL call fails."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Initialize the error."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModeBinding:
    """Binding between an Alexa mode value and an HA-facing label."""

    instance: str
    alexa_mode: str
    label: str


@dataclass(frozen=True)
class DirectionControls:
    """Transom-style Direct/Exhaust direction controller."""

    instance: str
    direct_mode: ModeBinding
    exhaust_mode: ModeBinding


@dataclass(frozen=True)
class TemperatureRange:
    """Discovered set-temperature minimum, maximum, and precision."""

    minimum: float
    maximum: float
    precision: float


@dataclass(frozen=True)
class AutoClimateControls:
    """Auto toggle and set-temperature range. Present only when both were discovered."""

    toggle_instance: str
    set_temperature_instance: str
    set_temperature_range: TemperatureRange


@dataclass(frozen=True)
class FanControlMap:
    """Discovered speed modes and optional direction, temperature, and auto climate."""

    speed_instance: str
    speed_modes: tuple[ModeBinding, ...]
    direction: DirectionControls | None = None
    air_temperature_instance: str | None = None
    auto_climate: AutoClimateControls | None = None


@dataclass
class CapabilityState:
    """Parsed state for a single endpoint feature."""

    name: str
    instance: str | None = None
    power_state: str | None = None
    mode_value: str | None = None
    range_value: float | None = None
    toggle_enabled: bool | None = None


@dataclass
class FanDevice:
    """A discovered Vornado Transom-compatible fan endpoint."""

    endpoint_id: str
    appliance_key: str
    name: str
    serial_number: str | None
    model: str | None
    manufacturer: str | None
    controls: FanControlMap
    power: str | None = None
    speed_mode: str | None = None
    direction_mode: str | None = None
    air_temperature: float | None = None
    auto_on: bool | None = None
    target_temperature: float | None = None
    available: bool = True
