import logging
import requests
import json
import asyncio
from typing import Callable, Optional

_LOGGER = logging.getLogger(__name__)

class SavantClient:
    def __init__(self, host, username, password):
        self._host = host
        self._username = username
        self._password = password
        self._base_url = f"http://{host}:8081"
        self._ws_url = f"ws://{host}:8082"
        self._zones = {}
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_callbacks: list[Callable] = []
        self._ws_running = False

    def register_callback(self, callback: Callable) -> Callable:
        """Register a callback for WebSocket state updates.

        Returns a function to unregister the callback.
        """
        self._ws_callbacks.append(callback)

        def unregister():
            if callback in self._ws_callbacks:
                self._ws_callbacks.remove(callback)

        return unregister

    async def start_websocket(self) -> None:
        """Start the WebSocket listener for real-time updates."""
        if self._ws_running:
            return

        self._ws_running = True
        self._ws_task = asyncio.create_task(self._websocket_loop())
        _LOGGER.info(f"Started WebSocket listener for {self._ws_url}")

    async def stop_websocket(self) -> None:
        """Stop the WebSocket listener."""
        self._ws_running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
        _LOGGER.info("Stopped WebSocket listener")

    async def _websocket_loop(self) -> None:
        """Main WebSocket connection loop with reconnection."""
        import aiohttp

        while self._ws_running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self._ws_url) as ws:
                        _LOGGER.info(f"WebSocket connected to {self._ws_url}")

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    await self._handle_ws_message(data)
                                except json.JSONDecodeError as e:
                                    _LOGGER.warning(f"Invalid WebSocket JSON: {e}")
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                _LOGGER.error(f"WebSocket error: {ws.exception()}")
                                break
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                _LOGGER.info("WebSocket closed by server")
                                break

            except asyncio.CancelledError:
                raise
            except Exception as e:
                _LOGGER.error(f"WebSocket connection error: {e}")

            if self._ws_running:
                _LOGGER.info("WebSocket reconnecting in 5 seconds...")
                await asyncio.sleep(5)

    async def _handle_ws_message(self, data: dict) -> None:
        """Handle incoming WebSocket message."""
        event_type = data.get("type")
        event_data = data.get("data", {})

        _LOGGER.debug(f"WebSocket message: {event_type} - {event_data}")

        # Notify all registered callbacks
        for callback in self._ws_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event_type, event_data)
                else:
                    callback(event_type, event_data)
            except Exception as e:
                _LOGGER.error(f"WebSocket callback error: {e}")

    def get_zones(self):
        """Retrieve zones and services from the REST Relay."""
        _LOGGER.info("Getting zones from Savant Relay...")
        try:
            response = requests.get(f"{self._base_url}/zones", timeout=10)
            response.raise_for_status()
            data = response.json()

            self._zones = data.get('zones', {})
            _LOGGER.info(f"Discovery complete. Found {len(self._zones)} zones.")
            return self._zones
        except Exception as e:
            _LOGGER.error(f"Failed to get zones: {e}")
            return {}

    def get_lights(self):
        """Retrieve light entities from the REST Relay."""
        _LOGGER.info("Getting lights from Savant Relay...")
        try:
            response = requests.get(f"{self._base_url}/lights", timeout=10)
            response.raise_for_status()
            data = response.json()

            lights = data.get('lights', [])
            _LOGGER.info(f"Found {len(lights)} lights.")
            return lights
        except Exception as e:
            _LOGGER.error(f"Failed to get lights: {e}")
            return []

    def get_state(self):
        """Retrieve current state of all components from the REST Relay."""
        try:
            response = requests.get(f"{self._base_url}/state", timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get('components', {})
        except Exception as e:
            _LOGGER.error(f"Failed to get state: {e}")
            return {}

    def get_light_status(self):
        """Retrieve current light levels from the REST Relay (via Lutron)."""
        try:
            response = requests.get(f"{self._base_url}/lights/status", timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get('lights', {})
        except Exception as e:
            _LOGGER.error(f"Failed to get light status: {e}")
            return {}

    def get_zone_states(self):
        """Retrieve real-time zone states from syslog event cache."""
        try:
            response = requests.get(f"{self._base_url}/zones/state", timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get('zones', {})
        except Exception as e:
            _LOGGER.error(f"Failed to get zone states: {e}")
            return {}

    def get_services(self, zone_name):
        """Return a list of services for a given zone."""
        if zone_name in self._zones:
            return self._zones[zone_name]['services']
        return []

    def send_command(self, zone, component, logical_component, service, variant_id, command, arguments=None):
        """Send a command via REST to Relay.

        Args:
            zone: Zone name (e.g., "Family Room")
            component: Component name (e.g., "Lutron")
            logical_component: Logical component (e.g., "Lighting_controller")
            service: Service type (e.g., "SVC_ENV_LIGHTING")
            variant_id: Service variant ID (e.g., "1")
            command: Command to execute (e.g., "DimmerSet")
            arguments: Optional dict of command arguments (e.g., {"Address1": "14", "DimmerLevel": "100"})
        """
        payload = {
            "zone": zone,
            "component": component,
            "logicalComponent": logical_component,
            "service": service,
            "serviceVariantID": str(variant_id),
            "command": command
        }
        if arguments:
            payload["arguments"] = arguments
        _LOGGER.debug(f"Sending Savant command: {payload}")
        try:
            response = requests.post(
                f"{self._base_url}/command",
                json=payload,
                timeout=5
            )
            response.raise_for_status()
            _LOGGER.debug("Command sent successfully")
            return True
        except Exception as e:
            _LOGGER.error(f"Failed to send command: {e}")
            return False
