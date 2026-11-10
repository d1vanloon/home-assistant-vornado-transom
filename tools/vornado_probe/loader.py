"""Import the integration's Alexa modules without importing Home Assistant."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

PACKAGE = "custom_components.vornado_transom"
PACKAGE_DIR = Path(__file__).resolve().parents[2] / "custom_components" / "vornado_transom"


def load(name: str) -> types.ModuleType:
    """Import `custom_components.vornado_transom.<name>`, e.g. `load("alexa.endpoints")`.

    The integration package's `__init__` imports Home Assistant, so it is
    replaced by a bare package whose path still resolves the real submodules.
    """
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(PACKAGE_DIR)]
        package.__package__ = PACKAGE
        sys.modules[PACKAGE] = package
    return importlib.import_module(f"{PACKAGE}.{name}")
