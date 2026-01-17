"""The Savant Control integration."""
import logging
import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv

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

def setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Savant Control integration."""
    conf = config[DOMAIN]
    host = conf[CONF_HOST]
    username = conf[CONF_USERNAME]
    password = conf[CONF_PASSWORD]

    client = SavantClient(host, username, password)
    
    # Store client in hass.data for platforms to access
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["client"] = client

    # Load platforms
    hass.helpers.discovery.load_platform("media_player", DOMAIN, {}, config)
    hass.helpers.discovery.load_platform("light", DOMAIN, {}, config)

    return True
