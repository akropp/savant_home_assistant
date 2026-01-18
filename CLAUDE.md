# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom integration for Savant home automation systems. It consists of two main components:

1. **Relay Server** (`savant_relay.py`) - Python 2.7 script that runs on the Savant host, bridging REST API to Savant's internal SOAP/UDP protocol
2. **HA Integration** (`custom_components/savant_control/`) - Python 3.11 Home Assistant integration that communicates with the relay

## Architecture

```
Home Assistant          Savant Host (192.168.1.218)
     │                         │
     │  HTTP REST (port 8081)  │
     ├────────────────────────►│ savant_relay.py
     │                         │     │
     │                         │     ├─► SOAP/UDP to UIS (dynamic port via Avahi)
     │                         │     ├─► SQLite queries (serviceImplementation.sqlite)
     │                         │     ├─► Persistent Lutron connection (192.168.1.249:23)
     │                         │     └─► inotify watch on avc.plist files
```

The relay maintains:
- **StateCache**: Thread-safe cache for component states and light levels
- **Lutron listener thread**: Persistent TCP connection receiving real-time `~OUTPUT` updates
- **Plist watcher thread**: inotify-based monitoring of `/home/RPM/GNUstep/Library/ApplicationSupport/RacePointMedia/statusfiles/*.avc.plist`

## Key Files

- `savant_relay.py` - **Python 2.7** (runs on Savant host's embedded Linux)
- `custom_components/savant_control/__init__.py` - Integration setup, loads platforms
- `custom_components/savant_control/savant_client.py` - REST client for relay API
- `custom_components/savant_control/light.py` - Light entities (dimmers/switches)
- `custom_components/savant_control/media_player.py` - Zone-based media players

## Development Commands

```bash
# Deploy relay to Savant host
cat savant_relay.py | ssh RPM@192.168.1.218 "cat > /home/RPM/savant_relay.py"

# Restart relay on Savant host
ssh RPM@192.168.1.218 "pkill -f 'python.*savant_relay'; cd /home/RPM && nohup python -u savant_relay.py > /tmp/relay.log 2>&1 &"

# Check relay log
ssh RPM@192.168.1.218 "tail -50 /tmp/relay.log"

# Test relay endpoints
curl http://192.168.1.218:8081/zones
curl http://192.168.1.218:8081/lights
curl http://192.168.1.218:8081/lights/status
curl http://192.168.1.218:8081/state
```

## Important Constraints

- **Relay is Python 2.7**: The Savant host runs an old embedded Linux. Use `print "text"` syntax, no f-strings, `BaseHTTPServer` instead of `http.server`.
- **Savant credentials**: SSH user is `RPM`, password is `RPM`
- **Lutron integration**: Lights are controlled via Lutron RadioRA2 at 192.168.1.249:23 (credentials: lutron/integration)
- **UIS port is dynamic**: Discovered via Avahi (`_uis_Kropp_ssp._udp`), falls back to 45600

## Relay API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/zones` | GET | All zones and services from SQLite |
| `/lights` | GET | Light entities with address/type info |
| `/lights/status` | GET | Real-time light levels (from cache) |
| `/state` | GET | Component states from cached plist data |
| `/command` | POST | Send SOAP command to Savant UIS |
