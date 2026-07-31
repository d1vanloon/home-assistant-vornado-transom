"""Constants for the Vornado Transom integration."""

from datetime import timedelta
import logging

DOMAIN = "vornado_transom"
CONF_LOGIN_DATA = "login_data"
SCAN_INTERVAL = timedelta(seconds=60)
LOGGER = logging.getLogger(__package__)
MANUFACTURER = "Vornado"
