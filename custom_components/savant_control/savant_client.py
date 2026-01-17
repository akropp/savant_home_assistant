import logging
import requests
import json

_LOGGER = logging.getLogger(__name__)

class SavantClient:
    def __init__(self, host, username, password):
        self._host = host
        self._username = username
        self._password = password
        self._base_url = f"http://{host}:8081"
        self._zones = {}

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
            return []

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
