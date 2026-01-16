import logging
from typing import Optional

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerDeviceClass,
)
from homeassistant.const import STATE_ON, STATE_OFF, STATE_IDLE

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Savant Media Player platform."""
    client = hass.data[DOMAIN]["client"]
    
    # Perform discovery (this might block, ideally should be async or cached)
    # Since we are in async_setup_platform, we should run this in executor
    zones = await hass.async_add_executor_job(client.get_zones)
    
    entities = []
    for zone_name, zone_data in zones.items():
        # Only create entities for zones that have services
        if zone_data.get('services'):
            entities.append(SavantMediaPlayer(client, zone_name, zone_data))
    
    async_add_entities(entities)

class SavantMediaPlayer(MediaPlayerEntity):
    """Representation of a Savant Zone as a Media Player."""

    def __init__(self, client, zone_name, zone_data):
        self._client = client
        self._zone_name = zone_name
        self._zone_data = zone_data
        self._name = f"Savant {zone_name}"
        self._state = STATE_IDLE
        self._source = None

        # Services from relay: list of dicts with alias, type, component,
        # logicalComponent, serviceVariantID, service
        raw_services = zone_data.get('services', [])
        self._services = {}
        for svc in raw_services:
            if isinstance(svc, dict) and svc.get('alias'):
                self._services[svc['alias']] = svc

        self._source_list = sorted(list(self._services.keys()))

        # Priority list for volume control
        volume_priorities = [
            "SVC_SETTINGS_SURROUNDSOUND",
            "SVC_SETTINGS_EQUALIZER",
            "SVC_AV_TV",
            "SVC_AV_SONOS",
            "SVC_AV_LIVEMEDIAQUERY_SAVANTMEDIAAUDIO",
            "SVC_AV_EXTERNALMEDIASERVER",
        ]

        self._volume_service = None

        # Check priorities in order
        for priority in volume_priorities:
            for alias, svc in self._services.items():
                if priority in svc.get('type', '') or priority in svc.get('service', ''):
                    self._volume_service = svc
                    break
            if self._volume_service:
                break

        # Fallback: use the first service
        if not self._volume_service and self._services:
            self._volume_service = list(self._services.values())[0]

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def source(self):
        return self._source

    @property
    def source_list(self):
        return self._source_list

    @property
    def supported_features(self):
        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )
        if self._volume_service:
            features |= (
                MediaPlayerEntityFeature.VOLUME_STEP
                | MediaPlayerEntityFeature.VOLUME_MUTE
            )
        return features

    @property
    def device_class(self):
        return MediaPlayerDeviceClass.SPEAKER

    def _send_service_command(self, service_info, command):
        """Send a command for a service."""
        self._client.send_command(
            zone=self._zone_name,
            component=service_info.get('component', ''),
            logical_component=service_info.get('logicalComponent', ''),
            service=service_info.get('type', ''),
            variant_id=service_info.get('serviceVariantID', '1'),
            command=command
        )

    def turn_on(self):
        """Turn the media player on."""
        target_source = self._source or (self._source_list[0] if self._source_list else None)
        if target_source:
            self.select_source(target_source)
        else:
            _LOGGER.warning(f"No sources available to turn on {self._name}")

    def turn_off(self):
        """Turn the media player off."""
        if self._source and self._source in self._services:
            svc = self._services[self._source]
            self._send_service_command(svc, "PowerOff")

        if self._volume_service:
            self._send_service_command(self._volume_service, "PowerOff")

        self._state = STATE_OFF

    def volume_up(self):
        """Volume up the media player."""
        if self._volume_service:
            self._send_service_command(self._volume_service, "IncreaseVolume")

    def volume_down(self):
        """Volume down the media player."""
        if self._volume_service:
            self._send_service_command(self._volume_service, "DecreaseVolume")

    def mute_volume(self, mute):
        """Mute the volume."""
        if self._volume_service:
            cmd = "MuteOn" if mute else "MuteOff"
            self._send_service_command(self._volume_service, cmd)

    def select_source(self, source):
        """Select input source."""
        if source in self._services:
            svc = self._services[source]
            self._send_service_command(svc, "PowerOn")
            self._source = source
            self._state = STATE_ON
