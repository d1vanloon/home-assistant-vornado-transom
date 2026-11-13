"""Import Alexa modules without importing Home Assistant.

The integration package __init__ imports Home Assistant. Tests replace that
package entry with a path-only module so the alexa submodules still load.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

PACKAGE = "custom_components.vornado_transom"
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "vornado_transom"


def load(name: str) -> types.ModuleType:
    """Import ``custom_components.vornado_transom.<name>``."""
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(PACKAGE_DIR)]  # type: ignore[attr-defined]
        package.__package__ = PACKAGE
        sys.modules[PACKAGE] = package
    return importlib.import_module(f"{PACKAGE}.{name}")
