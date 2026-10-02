"""Build aioamazondevices clients for the copy Home Assistant already installed.

Home Assistant shares one aioamazondevices install with every integration.
2026.8 ships 14.2.2. 2026.9 and later require the save_data argument added in
15.0.0. Passing that argument only when the installed constructor accepts it
lets this integration follow Home Assistant's version instead of replacing it.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from aioamazondevices.api import AmazonEchoApi
from aioamazondevices.http_wrapper import AmazonHttpWrapper, AmazonSessionStateData
from aiohttp import ClientSession

# AmazonSaveDataConfig does not exist before aioamazondevices 15.0.0.
try:
    from aioamazondevices.structures import (
        AmazonSaveDataConfig as _SaveDataConfig,
    )
except ImportError:
    _SaveDataConfig = None


def _accepts_save_data(target: Any) -> bool:
    """Return whether this library build requires AmazonSaveDataConfig."""
    return "save_data" in inspect.signature(target).parameters


def _save_data(storage_path: str) -> Any:
    """Build the debug-storage config used by aioamazondevices 15+."""
    if _SaveDataConfig is None:
        raise RuntimeError("aioamazondevices does not provide AmazonSaveDataConfig")
    return _SaveDataConfig(path=Path(storage_path))


def amazon_echo_api(
    session: ClientSession,
    username: str,
    password: str,
    storage_path: str,
) -> AmazonEchoApi:
    """Return an AmazonEchoApi for the installed library."""
    if _accepts_save_data(AmazonEchoApi):
        return AmazonEchoApi(
            session,
            username,
            password,
            save_data=_save_data(storage_path),
        )
    return AmazonEchoApi(session, username, password)


def amazon_http_wrapper(
    session: ClientSession,
    session_state: AmazonSessionStateData,
    storage_path: str,
) -> AmazonHttpWrapper:
    """Return an AmazonHttpWrapper for the installed library."""
    if _accepts_save_data(AmazonHttpWrapper):
        return AmazonHttpWrapper(
            session,
            session_state,
            save_data=_save_data(storage_path),
        )
    return AmazonHttpWrapper(session, session_state)
