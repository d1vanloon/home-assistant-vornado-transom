"""Discover Vornado fan ModeController bindings."""

from __future__ import annotations

import json
import re
from typing import Any

from .models import DirectionControls, FanControlMap, ModeBinding

SPEED_FALLBACK_LABELS = ("Low", "Medium", "High", "Turbo")
DIRECTION_FALLBACK_LABELS = ("Direct", "Exhaust")

_NON_ALNUM_SPACE = re.compile(r"[^a-z0-9 ]")
_MULTI_SPACE = re.compile(r"\s+")


def normalize_fan_label(input_value: str) -> str:
    """Normalize a friendly name for keyword matching."""
    return _MULTI_SPACE.sub(
        " ", _NON_ALNUM_SPACE.sub("", input_value.lower())
    ).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def friendly_names(capability: dict[str, Any]) -> list[str]:
    """Extract normalized friendly names from a capability."""
    names: list[str] = []
    resources = _as_dict(capability.get("resources"))
    for friendly in resources.get("friendlyNames") or []:
        if not isinstance(friendly, dict):
            continue
        value = _as_dict(friendly.get("value"))
        text = value.get("text")
        if text:
            names.append(normalize_fan_label(text))
            continue
        asset_id = value.get("assetId")
        if asset_id:
            names.append(normalize_fan_label(str(asset_id).rsplit(".", maxsplit=1)[-1]))
    return names


def _has_friendly_name(capability: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    """Return True if any friendly name contains a keyword."""
    normalized_keywords = tuple(normalize_fan_label(keyword) for keyword in keywords)
    return any(
        any(keyword in name for keyword in normalized_keywords)
        for name in friendly_names(capability)
    )


def _is_mode_controller(capability: dict[str, Any]) -> bool:
    return capability.get("interfaceName") == "Alexa.ModeController"


def _is_not_read_only(capability: dict[str, Any]) -> bool:
    return _as_dict(capability.get("properties")).get("readOnly") is not True


def _has_instance(capability: dict[str, Any]) -> bool:
    return bool(capability.get("instance"))


def match_mode_speed(capability: dict[str, Any]) -> bool:
    """Match a Vornado Transom speed ModeController."""
    configuration = _as_dict(capability.get("configuration"))
    supported_modes = configuration.get("supportedModes") or []
    return (
        _is_mode_controller(capability)
        and _is_not_read_only(capability)
        and _has_instance(capability)
        and _has_friendly_name(capability, ("fan speed", "speed"))
        and configuration.get("ordered") is True
        and len(supported_modes) == 4
    )


def match_mode_direction(capability: dict[str, Any]) -> bool:
    """Match a Vornado Transom direction ModeController."""
    configuration = _as_dict(capability.get("configuration"))
    supported_modes = configuration.get("supportedModes") or []
    return (
        _is_mode_controller(capability)
        and _is_not_read_only(capability)
        and _has_instance(capability)
        and _has_friendly_name(capability, ("direction", "wind"))
        and configuration.get("ordered") is True
        and len(supported_modes) == 2
    )


def _label_from_mode(
    mode: dict[str, Any],
    *,
    index: int,
    fallbacks: tuple[str, ...],
) -> str:
    """Extract an HA-facing label for a supported mode."""
    friendly_names = (mode.get("modeResources") or {}).get("friendlyNames") or []
    for friendly in friendly_names:
        if friendly.get("@type") == "text":
            text = (friendly.get("value") or {}).get("text")
            if text:
                return str(text)
    for friendly in friendly_names:
        asset_id = (friendly.get("value") or {}).get("assetId")
        if asset_id:
            return str(asset_id).rsplit(".", maxsplit=1)[-1]
    if 0 <= index < len(fallbacks):
        return fallbacks[index]
    return str(mode.get("value") or f"Mode{index + 1}")


def _alexa_mode(mode: Any) -> str | None:
    if not isinstance(mode, dict) or mode.get("value") is None:
        return None
    return str(mode["value"])


def _build_speed_modes(capability: dict[str, Any]) -> tuple[ModeBinding, ...]:
    instance = str(capability["instance"])
    modes = _as_dict(capability.get("configuration")).get("supportedModes") or []
    bindings: list[ModeBinding] = []
    for index, mode in enumerate(modes):
        alexa_mode = _alexa_mode(mode)
        if alexa_mode is None:
            return ()
        bindings.append(
            ModeBinding(
                instance=instance,
                alexa_mode=alexa_mode,
                label=_label_from_mode(
                    mode, index=index, fallbacks=SPEED_FALLBACK_LABELS
                ),
            )
        )
    return tuple(bindings)


def _build_direction_modes(
    capability: dict[str, Any],
) -> tuple[ModeBinding, ModeBinding] | None:
    instance = str(capability["instance"])
    modes = _as_dict(capability.get("configuration")).get("supportedModes") or []
    bindings: list[ModeBinding] = []
    for index, mode in enumerate(modes):
        alexa_mode = _alexa_mode(mode)
        if alexa_mode is None:
            return None
        bindings.append(
            ModeBinding(
                instance=instance,
                alexa_mode=alexa_mode,
                label=_label_from_mode(
                    mode, index=index, fallbacks=DIRECTION_FALLBACK_LABELS
                ),
            )
        )

    direct = next(
        (
            binding
            for binding in bindings
            if "direct" in normalize_fan_label(binding.label)
        ),
        None,
    )
    exhaust = next(
        (
            binding
            for binding in bindings
            if "exhaust" in normalize_fan_label(binding.label)
        ),
        None,
    )
    if direct is None or exhaust is None:
        direct = bindings[0]
        exhaust = bindings[1]
    return direct, exhaust


def parse_capabilities(raw: Any) -> list[dict[str, Any]] | None:
    """Parse legacyAppliance.capabilities which may be a JSON string or list."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        parsed = raw
    if not isinstance(parsed, list):
        return None
    return [item for item in parsed if isinstance(item, dict)]


def discover_vornado_controls(capabilities: Any) -> tuple[FanControlMap | None, str]:
    """Discover fans that expose a 4-speed ModeController.

    A fan with no matching direction controller is returned as speed-only.
    A matching direction controller that cannot build both modes rejects
    the whole capability list. The string is why discovery failed, or empty
    when a fan was found.
    """
    parsed = (
        capabilities
        if isinstance(capabilities, list)
        and all(isinstance(item, dict) for item in capabilities)
        else parse_capabilities(capabilities)
    )
    if not parsed:
        return None, "capability list is empty"

    speed_cap = next((cap for cap in parsed if match_mode_speed(cap)), None)
    if speed_cap is None:
        return None, "no writable, ordered, 4-mode speed ModeController"

    speed_modes = _build_speed_modes(speed_cap)
    if len(speed_modes) != 4:
        return None, "speed ModeController has a mode with no value"

    direction: DirectionControls | None = None
    direction_cap = next((cap for cap in parsed if match_mode_direction(cap)), None)
    if direction_cap is not None:
        built = _build_direction_modes(direction_cap)
        if built is None:
            return None, "matching direction controller has a mode with no value"
        direct_mode, exhaust_mode = built
        direction = DirectionControls(
            instance=str(direction_cap["instance"]),
            direct_mode=direct_mode,
            exhaust_mode=exhaust_mode,
        )

    return (
        FanControlMap(
            speed_instance=str(speed_cap["instance"]),
            speed_modes=speed_modes,
            direction=direction,
        ),
        "",
    )
