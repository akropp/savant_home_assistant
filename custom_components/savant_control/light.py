"""Savant Light platform for Home Assistant."""
import logging
from typing import Any, Optional

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass: HomeAssistant, config, async_add_entities, discovery_info=None):
    """Set up the Savant Light platform."""
    client = hass.data[DOMAIN]["client"]

    # Get lights from relay
    lights = await hass.async_add_executor_job(client.get_lights)

    entities = []
    for light_data in lights:
        entities.append(SavantLight(client, light_data))

    _LOGGER.info(f"Adding {len(entities)} Savant lights")
    async_add_entities(entities)


class SavantLight(LightEntity):
    """Representation of a Savant Light."""

    def __init__(self, client, light_data: dict):
        """Initialize the light."""
        self._client = client
        self._light_data = light_data

        self._zone = light_data['zone']
        self._light_name = light_data['name']
        self._address = light_data['address']
        self._is_dimmer = light_data['isDimmer']
        self._dimmer_command = light_data.get('dimmerCommand', 'DimmerSet')
        self._fade_time = light_data.get('fadeTime', 0)
        self._delay_time = light_data.get('delayTime', 0)

        # Service info for commands
        self._component = light_data.get('component', 'Lutron')
        self._logical_component = light_data.get('logicalComponent', 'Lighting_controller')
        self._service_variant_id = light_data.get('serviceVariantID', '1')
        self._service = light_data.get('service', 'SVC_ENV_LIGHTING')

        # State
        self._is_on = False
        self._brightness = 255  # 0-255 scale for HA

        # Unique ID
        self._attr_unique_id = f"savant_light_{self._zone}_{self._light_name}".replace(" ", "_").lower()

    @property
    def name(self) -> str:
        """Return the display name of this light."""
        return f"{self._zone} {self._light_name}"

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self._is_on

    @property
    def brightness(self) -> Optional[int]:
        """Return the brightness of the light (0-255)."""
        if self._is_dimmer:
            return self._brightness
        return None

    @property
    def color_mode(self) -> ColorMode:
        """Return the color mode of the light."""
        if self._is_dimmer:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> set:
        """Return the set of supported color modes."""
        if self._is_dimmer:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    def _send_command(self, command: str, arguments: dict = None):
        """Send a command to the light."""
        self._client.send_command(
            zone=self._zone,
            component=self._component,
            logical_component=self._logical_component,
            service=self._service,
            variant_id=self._service_variant_id,
            command=command,
            arguments=arguments
        )

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if self._is_dimmer:
            # Get brightness from kwargs, default to 100%
            brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
            # Convert HA brightness (0-255) to Savant (0-100)
            dimmer_level = round(brightness * 100 / 255)

            self._send_command(self._dimmer_command, {
                "Address1": str(self._address),
                "DimmerLevel": str(dimmer_level),
                "FadeTime": str(self._fade_time),
                "DelayTime": str(self._delay_time)
            })
            self._brightness = brightness
        else:
            # Switch - use SwitchOn
            self._send_command("SwitchOn", {
                "Address1": str(self._address)
            })

        self._is_on = True

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        if self._is_dimmer:
            self._send_command(self._dimmer_command, {
                "Address1": str(self._address),
                "DimmerLevel": "0",
                "FadeTime": str(self._fade_time),
                "DelayTime": str(self._delay_time)
            })
        else:
            # Switch - use SwitchOff
            self._send_command("SwitchOff", {
                "Address1": str(self._address)
            })

        self._is_on = False
        if self._is_dimmer:
            self._brightness = 0
