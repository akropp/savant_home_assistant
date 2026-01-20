import logging
from datetime import timedelta
from typing import Optional, Callable

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerDeviceClass,
)
from homeassistant.const import STATE_ON, STATE_OFF, STATE_IDLE
from homeassistant.core import callback

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Polling interval - fallback, WebSocket provides real-time updates
SCAN_INTERVAL = timedelta(seconds=30)

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
        self._volume_level = None
        self._is_muted = None

        # Unique ID for the entity
        self._attr_unique_id = f"savant_media_{zone_name}".replace(" ", "_").lower()

        # Services from relay: list of dicts with alias, type, component,
        # logicalComponent, serviceVariantID, service
        raw_services = zone_data.get('services', [])
        self._services = {}
        self._components = set()  # Track component names for state lookup
        for svc in raw_services:
            if isinstance(svc, dict) and svc.get('alias'):
                self._services[svc['alias']] = svc
                if svc.get('component'):
                    self._components.add(svc['component'])

        self._source_list = sorted(list(self._services.keys()))

        # Volume control configuration from relay
        self._volume_control = zone_data.get('volumeControl')
        if self._volume_control:
            _LOGGER.info(f"Zone {zone_name} volume control: {self._volume_control.get('component')} ({self._volume_control.get('serviceType')})")
            # Add the volume control component to tracked components
            if self._volume_control.get('stateComponent'):
                self._components.add(self._volume_control['stateComponent'])

        # Build volume service info for sending commands
        self._volume_service = None
        if self._volume_control:
            self._volume_service = {
                'component': self._volume_control.get('component'),
                'logicalComponent': self._volume_control.get('logicalComponent'),
                'serviceVariantID': self._volume_control.get('serviceVariantID', '1'),
                'type': self._volume_control.get('serviceType')
            }

        # WebSocket callback unregister function
        self._unregister_callback: Optional[Callable] = None

    async def async_added_to_hass(self) -> None:
        """Register for WebSocket updates when entity is added."""
        self._unregister_callback = self._client.register_callback(
            self._handle_ws_update
        )
        _LOGGER.debug(f"Registered WebSocket callback for {self._zone_name}")

    async def async_will_remove_from_hass(self) -> None:
        """Unregister WebSocket callback when entity is removed."""
        if self._unregister_callback:
            self._unregister_callback()
            self._unregister_callback = None

    @callback
    def _handle_ws_update(self, event_type: str, data: dict) -> None:
        """Handle WebSocket state update."""
        if event_type != "zone_state":
            return

        # Check if this update is for our zone
        if data.get("zone") != self._zone_name:
            return

        _LOGGER.debug(f"WebSocket update for {self._zone_name}: {data}")

        updated = False

        # Update power state
        if "power" in data:
            new_state = STATE_ON if data["power"] == "ON" else STATE_OFF
            if self._state != new_state:
                self._state = new_state
                updated = True

        # Update volume
        if "volume" in data:
            new_volume = max(0.0, min(1.0, data["volume"] / 100.0))
            if self._volume_level != new_volume:
                self._volume_level = new_volume
                updated = True

        # Update mute
        if "mute" in data:
            new_mute = data["mute"] == "ON"
            if self._is_muted != new_mute:
                self._is_muted = new_mute
                updated = True

        # Update source - component name from syslog matches service alias
        if "source" in data:
            new_source = data["source"]
            # Find matching service alias for this component
            for alias, svc in self._services.items():
                if svc.get('component') == new_source:
                    if self._source != alias:
                        self._source = alias
                        updated = True
                    break

        # Trigger Home Assistant state update
        if updated:
            self.async_write_ha_state()

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
    def volume_level(self) -> Optional[float]:
        """Return the volume level (0.0 to 1.0)."""
        return self._volume_level

    @property
    def is_volume_muted(self) -> Optional[bool]:
        """Return True if volume is muted."""
        return self._is_muted

    @property
    def supported_features(self):
        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )
        if self._volume_service:
            features |= (
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
                | MediaPlayerEntityFeature.VOLUME_MUTE
            )
        return features

    @property
    def device_class(self):
        return MediaPlayerDeviceClass.SPEAKER

    def update(self):
        """Fetch state from the relay."""
        try:
            all_states = self._client.get_state()
            zone_states = self._client.get_zone_states()

            # First check real-time zone state from syslog events
            zone_state = zone_states.get(self._zone_name, {})

            # Power state: prefer real-time zone state, fallback to component state
            power_on = False
            if 'power' in zone_state:
                power_on = zone_state['power'] == 'ON'
            else:
                # Fallback: check power state from any component in the zone
                for component_name in self._components:
                    if component_name in all_states:
                        states = all_states[component_name]
                        for key, value in states.items():
                            key_lower = key.lower()
                            if 'power' in key_lower and str(value).upper() == 'ON':
                                power_on = True
                                break
                    if power_on:
                        break

            # Volume/mute: prefer real-time zone state, fallback to component state
            volume = None
            muted = None

            # Check real-time zone state first
            if 'volume' in zone_state:
                try:
                    vol_val = int(zone_state['volume'])
                    # Real-time events are typically 0-100 scale
                    volume = max(0.0, min(1.0, vol_val / 100.0))
                except (ValueError, TypeError):
                    pass

            if 'mute' in zone_state:
                muted = zone_state['mute'] == 'ON'

            # Fallback to component state if no real-time data
            if volume is None and self._volume_control:
                state_component = self._volume_control.get('stateComponent')
                vol_key = self._volume_control.get('volumeStateKey')
                vol_scale = self._volume_control.get('volumeScale', 'percent')

                if state_component and state_component in all_states:
                    states = all_states[state_component]

                    if vol_key and vol_key in states:
                        try:
                            vol_val = int(states[vol_key])
                            if vol_scale == 'dB':
                                # Audio Switch: dB scale (-80 to 0)
                                volume = max(0.0, min(1.0, (vol_val + 80) / 80.0))
                            else:
                                # Receiver: percent scale (0-100)
                                volume = max(0.0, min(1.0, vol_val / 100.0))
                        except (ValueError, TypeError):
                            pass

            if muted is None and self._volume_control:
                state_component = self._volume_control.get('stateComponent')
                mute_key = self._volume_control.get('muteStateKey')

                if state_component and state_component in all_states:
                    states = all_states[state_component]
                    if mute_key and mute_key in states:
                        muted = str(states[mute_key]).upper() == 'ON'

            # Update state
            if power_on:
                self._state = STATE_ON
            else:
                self._state = STATE_OFF

            if volume is not None:
                self._volume_level = volume
            if muted is not None:
                self._is_muted = muted

            # Source: get from zone state (component name -> alias)
            if 'source' in zone_state:
                source_component = zone_state['source']
                for alias, svc in self._services.items():
                    if svc.get('component') == source_component:
                        self._source = alias
                        break

        except Exception as e:
            _LOGGER.error(f"Error updating state for {self._name}: {e}")

    def _send_service_command(self, service_info, command, arguments=None):
        """Send a command for a service."""
        self._client.send_command(
            zone=self._zone_name,
            component=service_info.get('component', ''),
            logical_component=service_info.get('logicalComponent', ''),
            service=service_info.get('type', ''),
            variant_id=service_info.get('serviceVariantID', '1'),
            command=command,
            arguments=arguments
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

    def set_volume_level(self, volume):
        """Set volume level (0.0 to 1.0)."""
        if not self._volume_service:
            return

        # Convert HA volume (0-1) to Savant volume
        vol_scale = self._volume_control.get('volumeScale', 'percent') if self._volume_control else 'percent'

        if vol_scale == 'dB':
            # dB scale: -80 (min) to 0 (max)
            # HA 0.0 -> -80dB, HA 1.0 -> 0dB
            savant_volume = int(-80 + (volume * 80))
        else:
            # Percent scale: 0 to 100
            savant_volume = int(volume * 100)

        self._send_service_command(
            self._volume_service,
            "SetVolume",
            {"VolumeValue": str(savant_volume)}
        )
        self._volume_level = volume

    def select_source(self, source):
        """Select input source."""
        if source in self._services:
            svc = self._services[source]
            self._send_service_command(svc, "PowerOn")
            self._source = source
            self._state = STATE_ON
