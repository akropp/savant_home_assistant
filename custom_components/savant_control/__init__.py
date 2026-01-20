"""The Savant Control integration."""
import logging
import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import discovery

from .savant_client import SavantClient

_LOGGER = logging.getLogger(__name__)

DOMAIN = "savant_control"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Savant Control integration."""
    _LOGGER.info("Savant Control async_setup starting...")

    conf = config[DOMAIN]
    host = conf[CONF_HOST]
    username = conf[CONF_USERNAME]
    password = conf[CONF_PASSWORD]

    _LOGGER.info(f"Savant Control connecting to host: {host}")

    client = SavantClient(host, username, password)

    # Store client in hass.data for platforms to access
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["client"] = client

    # Start WebSocket listener for real-time updates
    _LOGGER.info("Starting Savant WebSocket listener...")
    await client.start_websocket()
    _LOGGER.info("Savant WebSocket listener started")

    # Load platforms
    hass.async_create_task(
        discovery.async_load_platform(hass, "media_player", DOMAIN, {}, config)
    )
    hass.async_create_task(
        discovery.async_load_platform(hass, "light", DOMAIN, {}, config)
    )
    hass.async_create_task(
        discovery.async_load_platform(hass, "remote", DOMAIN, {}, config)
    )

    return True
