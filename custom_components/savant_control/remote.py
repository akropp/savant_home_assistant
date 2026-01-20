"""Savant Remote platform for Home Assistant."""
import logging
from typing import Any, Iterable, Optional, Callable

from homeassistant.components.remote import RemoteEntity, RemoteEntityFeature
from homeassistant.core import HomeAssistant, callback

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Map common HA remote commands to Savant command names
COMMAND_MAP = {
    # Navigation
    "up": "OSDCursorUp",
    "down": "OSDCursorDown",
    "left": "OSDCursorLeft",
    "right": "OSDCursorRight",
    "select": "OSDSelect",
    "enter": "OSDSelect",
    "ok": "OSDSelect",
    "back": "OSDBack",
    "menu": "Menu",
    "home": "Home",
    "info": "Info",
    "guide": "Guide",
    # Playback
    "play": "Play",
    "pause": "Pause",
    "play_pause": "PlayPause",
    "stop": "Stop",
    "fast_forward": "FastForward",
    "rewind": "Rewind",
    "skip_forward": "SkipForward",
    "skip_backward": "SkipBackward",
    "record": "Record",
    # Channel
    "channel_up": "ChannelUp",
    "channel_down": "ChannelDown",
    # Power
    "power": "PowerToggle",
    "power_on": "PowerOn",
    "power_off": "PowerOff",
    # Numbers
    "0": "Digit0",
    "1": "Digit1",
    "2": "Digit2",
    "3": "Digit3",
    "4": "Digit4",
    "5": "Digit5",
    "6": "Digit6",
    "7": "Digit7",
    "8": "Digit8",
    "9": "Digit9",
    # Other
    "mute": "MuteToggle",
    "volume_mute": "MuteToggle",
    "volume_up": "VolumeUp",
    "volume_down": "VolumeDown",
    "exit": "Exit",
    "previous": "Previous",
    "next": "Next",
}


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities, discovery_info=None
):
    """Set up the Savant Remote platform."""
    client = hass.data[DOMAIN]["client"]

    # Get zones from relay
    zones = await hass.async_add_executor_job(client.get_zones)

    entities = []
    for zone_name, zone_data in zones.items():
        # Only create remote for zones with media services
        services = zone_data.get("services", [])
        media_services = [
            s for s in services
            if s.get("type", "").startswith("SVC_AV_")
        ]
        if media_services:
            entities.append(SavantRemote(client, zone_name, zone_data))

    _LOGGER.info(f"Adding {len(entities)} Savant remotes")
    async_add_entities(entities)


class SavantRemote(RemoteEntity):
    """Representation of a Savant Zone Remote."""

    def __init__(self, client, zone_name: str, zone_data: dict):
        """Initialize the remote."""
        self._client = client
        self._zone_name = zone_name
        self._zone_data = zone_data
        self._name = f"Savant {zone_name} Remote"
        self._is_on = True  # Remote is always "on"
        self._current_source = None
        self._current_source_service = None
        self._is_muted = False  # Track mute state for toggle

        # Unique ID
        self._attr_unique_id = f"savant_remote_{zone_name}".replace(" ", "_").lower()

        # Build service lookup by component name
        self._services_by_component = {}
        for svc in zone_data.get("services", []):
            if isinstance(svc, dict) and svc.get("component"):
                comp = svc["component"]
                if comp not in self._services_by_component:
                    self._services_by_component[comp] = []
                self._services_by_component[comp].append(svc)

        # WebSocket callback
        self._unregister_callback: Optional[Callable] = None

    async def async_added_to_hass(self) -> None:
        """Register for WebSocket updates when entity is added."""
        self._unregister_callback = self._client.register_callback(
            self._handle_ws_update
        )

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
        if data.get("zone") != self._zone_name:
            return

        # Track current source
        if "source" in data:
            self._current_source = data["source"]
            # Find the best service for this source (prefer AV services)
            if self._current_source in self._services_by_component:
                services = self._services_by_component[self._current_source]
                # Prefer SVC_AV_ services for remote commands
                for svc in services:
                    if svc.get("type", "").startswith("SVC_AV_"):
                        self._current_source_service = svc
                        break
                else:
                    self._current_source_service = services[0] if services else None

            _LOGGER.debug(
                f"Remote {self._zone_name}: source={self._current_source}, "
                f"service={self._current_source_service}"
            )
            self.async_write_ha_state()

        # Track mute state for toggle logic
        if "mute" in data:
            self._is_muted = data["mute"] == "ON"

    @property
    def name(self) -> str:
        """Return the name of the remote."""
        return self._name

    @property
    def is_on(self) -> bool:
        """Return true if the remote is on."""
        return self._is_on

    @property
    def supported_features(self) -> RemoteEntityFeature:
        """Return supported features."""
        return RemoteEntityFeature.ACTIVITY

    @property
    def current_activity(self) -> Optional[str]:
        """Return the current activity (source)."""
        return self._current_source

    @property
    def activity_list(self) -> list[str]:
        """Return the list of available activities (sources)."""
        return list(self._services_by_component.keys())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "available_commands": list(COMMAND_MAP.keys()),
            "current_source_component": self._current_source,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the remote (select activity/source)."""
        activity = kwargs.get("activity")
        if activity and activity in self._services_by_component:
            # Power on this source
            services = self._services_by_component[activity]
            for svc in services:
                if svc.get("type", "").startswith("SVC_AV_"):
                    await self.hass.async_add_executor_job(
                        self._client.send_command,
                        self._zone_name,
                        svc.get("component", ""),
                        svc.get("logicalComponent", ""),
                        svc.get("type", ""),
                        svc.get("serviceVariantID", "1"),
                        "PowerOn",
                        None,
                    )
                    break

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the current source."""
        if self._current_source_service:
            await self.hass.async_add_executor_job(
                self._client.send_command,
                self._zone_name,
                self._current_source_service.get("component", ""),
                self._current_source_service.get("logicalComponent", ""),
                self._current_source_service.get("type", ""),
                self._current_source_service.get("serviceVariantID", "1"),
                "PowerOff",
                None,
            )

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send commands to the current source."""
        if not self._current_source_service:
            _LOGGER.warning(
                f"Remote {self._zone_name}: No active source to send commands to"
            )
            return

        num_repeats = kwargs.get("num_repeats", 1)
        delay_secs = kwargs.get("delay_secs", 0.1)

        for _ in range(num_repeats):
            for cmd in command:
                # Map common command names to Savant commands
                savant_cmd = COMMAND_MAP.get(cmd.lower(), cmd)

                # Handle mute toggle - Savant uses MuteOn/MuteOff, not MuteToggle
                if savant_cmd == "MuteToggle":
                    savant_cmd = "MuteOff" if self._is_muted else "MuteOn"
                    self._is_muted = not self._is_muted  # Optimistic update

                _LOGGER.debug(
                    f"Remote {self._zone_name}: Sending {savant_cmd} to "
                    f"{self._current_source}"
                )

                await self.hass.async_add_executor_job(
                    self._client.send_command,
                    self._zone_name,
                    self._current_source_service.get("component", ""),
                    self._current_source_service.get("logicalComponent", ""),
                    self._current_source_service.get("type", ""),
                    self._current_source_service.get("serviceVariantID", "1"),
                    savant_cmd,
                    None,
                )

                if delay_secs > 0 and num_repeats > 1:
                    import asyncio
                    await asyncio.sleep(delay_secs)

    def update(self) -> None:
        """Update the remote state."""
        # Get zone states for current source
        zone_states = self._client.get_zone_states()
        zone_state = zone_states.get(self._zone_name, {})

        if "source" in zone_state:
            self._current_source = zone_state["source"]
            if self._current_source in self._services_by_component:
                services = self._services_by_component[self._current_source]
                for svc in services:
                    if svc.get("type", "").startswith("SVC_AV_"):
                        self._current_source_service = svc
                        break

        if "mute" in zone_state:
            self._is_muted = zone_state["mute"] == "ON"
