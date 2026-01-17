# Savant Home Assistant Integration

A custom Home Assistant integration for controlling Savant home automation systems.

## Features

- **Light Control**: Individual control of Savant-connected lights
  - Dimmers with brightness control (0-100%)
  - Switches with on/off control
- **Media Player**: Zone-based media player entities for source selection and volume control

## Requirements

- Savant home automation system (tested with version 8.5)
- Access to the Savant host (SSH as RPM user)
- Home Assistant instance

## Installation

### 1. Set Up the Relay on Your Savant Host

The relay is a lightweight Python script that runs on your Savant host and bridges REST API calls to Savant's internal SOAP/UDP protocol.

1. Copy `savant_relay.py` to your Savant host:
   ```bash
   scp savant_relay.py RPM@<SAVANT_HOST_IP>:/home/RPM/savant_relay.py
   ```

2. SSH into your Savant host and start the relay:
   ```bash
   ssh RPM@<SAVANT_HOST_IP>
   python /home/RPM/savant_relay.py &
   ```

3. (Optional) Configure auto-start on boot:
   ```bash
   # Add to RPM user's crontab
   crontab -e
   # Add this line:
   @reboot sleep 30 && cd /home/RPM && python -u /home/RPM/savant_relay.py >> /home/RPM/relay.log 2>&1 &
   ```

4. Verify the relay is running:
   ```bash
   curl http://<SAVANT_HOST_IP>:8081/zones
   curl http://<SAVANT_HOST_IP>:8081/lights
   ```

### 2. Install the Home Assistant Integration

1. Copy the `custom_components/savant_control` folder to your Home Assistant configuration directory:
   ```bash
   cp -r custom_components/savant_control /path/to/homeassistant/config/custom_components/
   ```

   Or if using HACS, you can add this repository as a custom repository.

2. Add the following to your `configuration.yaml`:
   ```yaml
   savant_control:
     host: <SAVANT_HOST_IP>
     username: RPM
     password: <your_password>
   ```

3. Restart Home Assistant.

## Entities Created

### Lights

Each Savant light appears as a separate light entity:
- **Dimmers**: `light.savant_light_<zone>_<name>` with brightness support
- **Switches**: `light.savant_light_<zone>_<name>` with on/off only

Example entity IDs:
- `light.savant_light_kitchen_pendant`
- `light.savant_light_garage_garage_lights`

### Media Players

Each Savant zone with services appears as a media player:
- `media_player.savant_<zone>`

Media players support:
- Source selection (available services in the zone)
- Volume up/down
- Mute

## Relay API Reference

The relay exposes the following endpoints on port 8081:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/zones` | GET | Returns all zones and their services |
| `/lights` | GET | Returns all light entities with type info |
| `/command` | POST | Sends a command to Savant |

### Command Payload Example

```json
{
  "zone": "Kitchen",
  "component": "Lutron",
  "logicalComponent": "Lighting_controller",
  "service": "SVC_ENV_LIGHTING",
  "serviceVariantID": "1",
  "command": "DimmerSet",
  "arguments": {
    "Address1": "21",
    "DimmerLevel": "75",
    "FadeTime": "0",
    "DelayTime": "0"
  }
}
```

## Troubleshooting

### Relay won't start
- Check if port 8081 is already in use: `lsof -i :8081`
- Check the relay log: `cat /home/RPM/relay.log`

### Lights not responding
- Verify the relay is running and can discover the UIS port
- Check the relay log for "Discovered UIS port: XXXXX"
- The UIS port is dynamic; if Savant services restart, the relay needs to be restarted to rediscover the port

### Entities not appearing in Home Assistant
- Check Home Assistant logs for errors related to `savant_control`
- Verify the relay endpoints return data: `curl http://<SAVANT_HOST_IP>:8081/lights`

## Technical Details

- The relay discovers the Savant UIS (User Interface Server) port dynamically via Avahi/Bonjour
- Commands are sent via SOAP over UDP to the UIS
- Light entity information is read from the Savant SQLite database (`serviceImplementation.sqlite`)

## License

MIT License
