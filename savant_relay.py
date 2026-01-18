#!/usr/bin/env python
import BaseHTTPServer
import json
import sqlite3
import socket
import subprocess
import sys
import re
import os
import glob
import xml.etree.ElementTree as ET
import threading
import ctypes
import struct
import time
import select

# Config
DB_PATH = '/home/RPM/GNUstep/Library/ApplicationSupport/RacePointMedia/serviceImplementation.sqlite'
STATUS_PATH = '/home/RPM/GNUstep/Library/ApplicationSupport/RacePointMedia/statusfiles'
SAVANT_HOST = '127.0.0.1'
LISTEN_PORT = 8081

# Lutron config (from Lutron.avc.plist)
LUTRON_HOST = '192.168.1.249'
LUTRON_PORT = 23
LUTRON_USER = 'lutron'
LUTRON_PASS = 'integration'
# Disable persistent Lutron connection to avoid blocking Savant's connection
LUTRON_PERSISTENT = False

# inotify constants
IN_MODIFY = 0x00000002
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100

# Global state cache
class StateCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.component_states = {}  # component -> {state_key: value}
        self.light_levels = {}  # key -> {address, zone, name, level, is_on}
        self.last_update = 0

    def update_component(self, component_name, states):
        with self.lock:
            self.component_states[component_name] = states
            self.last_update = time.time()

    def update_light(self, key, data):
        with self.lock:
            self.light_levels[key] = data
            self.last_update = time.time()

    def get_components(self):
        with self.lock:
            return dict(self.component_states)

    def get_lights(self):
        with self.lock:
            return dict(self.light_levels)

STATE_CACHE = StateCache()


def query_lutron_levels(addresses):
    """Query Lutron for current output levels.

    Args:
        addresses: List of Lutron addresses to query

    Returns:
        Dict mapping address -> level (0-100)
    """
    import time
    levels = {}

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((LUTRON_HOST, LUTRON_PORT))

        # Login sequence
        time.sleep(0.3)
        sock.recv(1024)  # login prompt
        sock.send(LUTRON_USER + '\r\n')
        time.sleep(0.2)
        sock.recv(1024)  # password prompt
        sock.send(LUTRON_PASS + '\r\n')
        time.sleep(0.2)
        sock.recv(1024)  # GNET prompt

        # Query each address
        for addr in addresses:
            try:
                sock.send('?OUTPUT,%s,1\r\n' % addr)
                time.sleep(0.1)
                response = sock.recv(1024)
                # Parse response: ~OUTPUT,<addr>,1,<level>
                for line in response.split('\r\n'):
                    if line.startswith('~OUTPUT,'):
                        parts = line.split(',')
                        if len(parts) >= 4:
                            resp_addr = parts[1]
                            level = float(parts[3])
                            levels[resp_addr] = level
            except Exception as e:
                print "Error querying address %s: %s" % (addr, e)

        sock.close()
    except Exception as e:
        print "Lutron connection error: %s" % e

    return levels


def parse_gnustep_plist(filepath):
    """Parse a GNUstep plist file and return the States dict."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()

        # Find the main dict
        main_dict = root.find('dict')
        if main_dict is None:
            return {}

        # Parse dict into Python dict
        def parse_dict(dict_elem):
            result = {}
            keys = dict_elem.findall('key')
            for key_elem in keys:
                key = key_elem.text
                # Get the next sibling element (the value)
                next_elem = None
                found_key = False
                for child in dict_elem:
                    if found_key:
                        next_elem = child
                        break
                    if child == key_elem:
                        found_key = True

                if next_elem is None:
                    continue

                if next_elem.tag == 'string':
                    result[key] = next_elem.text or ''
                elif next_elem.tag == 'integer':
                    result[key] = int(next_elem.text) if next_elem.text else 0
                elif next_elem.tag == 'real':
                    result[key] = float(next_elem.text) if next_elem.text else 0.0
                elif next_elem.tag == 'dict':
                    result[key] = parse_dict(next_elem)
                elif next_elem.tag == 'array':
                    result[key] = [parse_dict(d) if d.tag == 'dict' else d.text for d in next_elem]
            return result

        data = parse_dict(main_dict)
        return data.get('States', {})
    except Exception as e:
        print "Error parsing plist %s: %s" % (filepath, e)
        return {}

def load_plist_to_cache(filepath):
    """Load a plist file into the state cache."""
    filename = os.path.basename(filepath)
    component_name = filename.replace('.avc.plist', '')

    states = parse_gnustep_plist(filepath)

    # Filter out non-state keys
    filtered_states = {}
    for key, value in states.items():
        if key.startswith('SavantHost') or key in ('login', 'password', 'ComponentProfileVersion', 'Version'):
            continue
        filtered_states[key] = value

    if filtered_states:
        STATE_CACHE.update_component(component_name, filtered_states)
        print "Updated cache for component: %s (%d states)" % (component_name, len(filtered_states))


def plist_watcher_thread():
    """Background thread that watches plist files for changes using inotify."""
    print "Starting plist file watcher..."

    # Load initial state
    plist_pattern = os.path.join(STATUS_PATH, '*.avc.plist')
    for plist_file in glob.glob(plist_pattern):
        load_plist_to_cache(plist_file)

    try:
        # Set up inotify
        libc = ctypes.CDLL('libc.so.6', use_errno=True)
        inotify_init = libc.inotify_init
        inotify_add_watch = libc.inotify_add_watch
        inotify_rm_watch = libc.inotify_rm_watch

        fd = inotify_init()
        if fd < 0:
            print "inotify_init failed, falling back to polling"
            plist_poller_thread()
            return

        # Watch the status directory
        watch_mask = IN_MODIFY | IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE
        wd = inotify_add_watch(fd, STATUS_PATH, watch_mask)
        if wd < 0:
            print "inotify_add_watch failed, falling back to polling"
            os.close(fd)
            plist_poller_thread()
            return

        print "inotify watching %s" % STATUS_PATH

        # Event structure: 4 bytes wd, 4 bytes mask, 4 bytes cookie, 4 bytes len, then name
        while True:
            try:
                # Use select to allow timeout for checking thread status
                ready, _, _ = select.select([fd], [], [], 5.0)
                if not ready:
                    continue

                # Read events
                buf = os.read(fd, 4096)
                if not buf:
                    continue

                offset = 0
                while offset < len(buf):
                    wd, mask, cookie, length = struct.unpack('iIII', buf[offset:offset+16])
                    offset += 16
                    if length > 0:
                        name = buf[offset:offset+length].rstrip('\x00')
                        offset += length

                        # Check if it's a plist file
                        if name.endswith('.avc.plist'):
                            filepath = os.path.join(STATUS_PATH, name)
                            if os.path.exists(filepath):
                                print "File changed: %s" % name
                                load_plist_to_cache(filepath)

            except Exception as e:
                print "inotify read error: %s" % e
                time.sleep(1)

    except Exception as e:
        print "inotify setup failed: %s, falling back to polling" % e
        plist_poller_thread()


def plist_poller_thread():
    """Fallback poller if inotify is not available."""
    print "Using polling mode for plist files"
    file_mtimes = {}

    while True:
        try:
            plist_pattern = os.path.join(STATUS_PATH, '*.avc.plist')
            for plist_file in glob.glob(plist_pattern):
                try:
                    mtime = os.path.getmtime(plist_file)
                    if plist_file not in file_mtimes or file_mtimes[plist_file] != mtime:
                        file_mtimes[plist_file] = mtime
                        load_plist_to_cache(plist_file)
                except OSError:
                    pass
            time.sleep(2)  # Poll every 2 seconds
        except Exception as e:
            print "Polling error: %s" % e
            time.sleep(5)


def lutron_listener_thread():
    """Background thread that maintains persistent Lutron connection for real-time updates."""
    print "Starting Lutron listener..."

    # Get light address mappings
    address_map = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        query = """
        SELECT le.addresses, z.name as zone, le.name as light_name
        FROM LightEntities le
        JOIN Zones z ON le.zoneID = z.id
        WHERE le.entityType IN ('Dimmer', 'Switch')
        """
        cursor.execute(query)
        for row in cursor.fetchall():
            addr_str = row[0]
            zone = row[1]
            name = row[2]
            if addr_str:
                addr = addr_str.split(',')[0]
                address_map[addr] = {'zone': zone, 'name': name}
        conn.close()
    except Exception as e:
        print "Error loading light addresses: %s" % e

    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(30)
            sock.connect((LUTRON_HOST, LUTRON_PORT))
            print "Connected to Lutron at %s:%d" % (LUTRON_HOST, LUTRON_PORT)

            # Login sequence
            time.sleep(0.3)
            sock.recv(1024)
            sock.send(LUTRON_USER + '\r\n')
            time.sleep(0.2)
            sock.recv(1024)
            sock.send(LUTRON_PASS + '\r\n')
            time.sleep(0.2)
            sock.recv(1024)

            # Query initial state for all addresses
            for addr in address_map.keys():
                try:
                    sock.send('?OUTPUT,%s,1\r\n' % addr)
                    time.sleep(0.05)
                except:
                    pass

            # Now listen for updates
            sock.settimeout(60)  # Longer timeout for listening
            buffer = ''

            while True:
                try:
                    data = sock.recv(1024)
                    if not data:
                        print "Lutron connection closed"
                        break

                    buffer += data

                    # Process complete lines
                    while '\r\n' in buffer:
                        line, buffer = buffer.split('\r\n', 1)
                        line = line.strip()

                        # Parse output level updates: ~OUTPUT,<addr>,1,<level>
                        if line.startswith('~OUTPUT,'):
                            parts = line.split(',')
                            if len(parts) >= 4:
                                addr = parts[1]
                                try:
                                    level = float(parts[3])
                                    if addr in address_map:
                                        info = address_map[addr]
                                        key = "%s_%s" % (info['zone'], info['name'])
                                        key = key.replace(' ', '_').lower()
                                        STATE_CACHE.update_light(key, {
                                            'address': addr,
                                            'zone': info['zone'],
                                            'name': info['name'],
                                            'level': level,
                                            'is_on': level > 0
                                        })
                                        print "Light update: %s = %.1f%%" % (key, level)
                                except ValueError:
                                    pass

                except socket.timeout:
                    # Send a keepalive query
                    try:
                        if address_map:
                            addr = list(address_map.keys())[0]
                            sock.send('?OUTPUT,%s,1\r\n' % addr)
                    except:
                        break

        except Exception as e:
            print "Lutron listener error: %s" % e

        print "Reconnecting to Lutron in 5 seconds..."
        time.sleep(5)


def discover_uis_port():
    """Discover UIS port via Avahi/Bonjour."""
    try:
        output = subprocess.check_output(
            ['avahi-browse', '-r', '-t', '-p', '_uis_Kropp_ssp._udp'],
            stderr=subprocess.STDOUT
        )
        # Parse output for port - format: ;eth0;IPv4;uis;...;address;port;...
        for line in output.split('\n'):
            if line.startswith('=') and 'IPv4' in line:
                parts = line.split(';')
                if len(parts) >= 9:
                    port = int(parts[8])
                    print "Discovered UIS port: %d" % port
                    return port
    except Exception as e:
        print "Avahi discovery failed: %s" % e
    # Fallback
    print "Using fallback UIS port 45600"
    return 45600

SAVANT_UIS_PORT = discover_uis_port()

# SOAP template for service commands
SOAP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:SOAP-ENC="http://www.w3.org/2003/05/soap-encoding" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:wsdl="http://tempuri.org/wsdl.xsd" xmlns:md="urn:rpm-metadatainterface" xmlns:ctl="urn:rpm-controlinterface" xmlns:rdm="urn:rpm-rdminterface" xmlns:rpm="urn:rpm-common" xmlns:sm="urn:rpm-stateManagementInterface" xmlns:smrdm="urn:sm-rdminterface" xmlns:snsr="urn:rpm-userSNSRInterface" xmlns:sync="urn:rpm-syncinterface"><SOAP-ENV:Body><ctl:serviceEventRequest><zoneString>{zone}</zoneString><componentString>{component}</componentString><logicalComponentString>{logical}</logicalComponentString><serviceString>{service}</serviceString><serviceVariantIDString>{variant}</serviceVariantIDString><commandString>{command}</commandString>{args}</ctl:serviceEventRequest></SOAP-ENV:Body></SOAP-ENV:Envelope>"""

class SavantRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/zones':
            self.handle_zones()
        elif self.path == '/lights':
            self.handle_lights()
        elif self.path == '/lights/status':
            self.handle_lights_status()
        elif self.path == '/state':
            self.handle_state()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/command':
            self.handle_command()
        else:
            self.send_error(404)

    def handle_zones(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Query the view which has all needed fields
            query = """
            SELECT
                zone,
                alias,
                component,
                logicalComponent,
                serviceVariantID,
                serviceType,
                service
            FROM ServiceImplementationZonedService
            WHERE alias IS NOT NULL
            """

            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()

            zones = {}
            for row in rows:
                zone_name = row[0]
                alias = row[1]
                component = row[2]
                logical = row[3]
                variant_id = row[4]
                svc_type = row[5]
                service = row[6]

                if zone_name not in zones:
                    zones[zone_name] = {'name': zone_name, 'services': []}

                zones[zone_name]['services'].append({
                    'alias': alias,
                    'type': svc_type,
                    'component': component,
                    'logicalComponent': logical,
                    'serviceVariantID': str(variant_id),
                    'service': service
                })

            response_data = {'zones': zones}

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response_data))

        except Exception as e:
            print "SQL Error:", e
            self.send_error(500, str(e))

    def handle_lights(self):
        """Return light entities with zone, name, address, type, and service info."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Get light entities with zone info
            query = """
            SELECT
                z.name as zone,
                le.name as light_name,
                le.addresses,
                le.entityType,
                le.dimmerCommand,
                le.fadeTime,
                le.delayTime
            FROM LightEntities le
            JOIN Zones z ON le.zoneID = z.id
            WHERE le.entityType IN ('Dimmer', 'Switch')
            ORDER BY z.name, le.name
            """
            cursor.execute(query)
            light_rows = cursor.fetchall()

            # Get lighting service info per zone
            svc_query = """
            SELECT zone, component, logicalComponent, serviceVariantID
            FROM ServiceImplementationZonedService
            WHERE serviceType = 'SVC_ENV_LIGHTING'
            """
            cursor.execute(svc_query)
            svc_rows = cursor.fetchall()
            conn.close()

            # Build zone -> service info map
            zone_service = {}
            for row in svc_rows:
                zone_service[row[0]] = {
                    'component': row[1],
                    'logicalComponent': row[2],
                    'serviceVariantID': str(row[3])
                }

            lights = []
            for row in light_rows:
                zone = row[0]
                name = row[1]
                addresses = row[2]
                entity_type = row[3]
                dimmer_cmd = row[4]
                fade_time = row[5] or 0
                delay_time = row[6] or 0

                # Extract first address (Address1)
                address = addresses.split(',')[0] if addresses else ''

                # Get service info for this zone
                svc_info = zone_service.get(zone, {})

                lights.append({
                    'zone': zone,
                    'name': name,
                    'address': address,
                    'entityType': entity_type,
                    'isDimmer': entity_type == 'Dimmer',
                    'dimmerCommand': dimmer_cmd or 'DimmerSet',
                    'fadeTime': fade_time,
                    'delayTime': delay_time,
                    'component': svc_info.get('component', 'Lutron'),
                    'logicalComponent': svc_info.get('logicalComponent', 'Lighting_controller'),
                    'serviceVariantID': svc_info.get('serviceVariantID', '1'),
                    'service': 'SVC_ENV_LIGHTING'
                })

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'lights': lights}))

        except Exception as e:
            print "Lights query error:", e
            self.send_error(500, str(e))

    def handle_lights_status(self):
        """Return current light levels from cache (populated by Lutron listener)."""
        try:
            # Get cached light status
            cached_lights = STATE_CACHE.get_lights()

            if cached_lights:
                # Return cached data
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'lights': cached_lights}))
                return

            # Fallback: query Lutron directly if cache is empty
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            query = """
            SELECT le.addresses, z.name as zone, le.name as light_name
            FROM LightEntities le
            JOIN Zones z ON le.zoneID = z.id
            WHERE le.entityType IN ('Dimmer', 'Switch')
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()

            address_map = {}
            addresses = []
            for row in rows:
                addr_str = row[0]
                zone = row[1]
                name = row[2]
                if addr_str:
                    addr = addr_str.split(',')[0]
                    addresses.append(addr)
                    address_map[addr] = {'zone': zone, 'name': name}

            levels = query_lutron_levels(addresses)

            status = {}
            for addr, info in address_map.items():
                key = "%s_%s" % (info['zone'], info['name'])
                key = key.replace(' ', '_').lower()
                level = levels.get(addr, 0)
                data = {
                    'address': addr,
                    'zone': info['zone'],
                    'name': info['name'],
                    'level': level,
                    'is_on': level > 0
                }
                status[key] = data
                STATE_CACHE.update_light(key, data)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'lights': status}))

        except Exception as e:
            print "Lights status error:", e
            self.send_error(500, str(e))

    def handle_state(self):
        """Return current state of all components from cache."""
        try:
            # Get cached component states
            components = STATE_CACHE.get_components()

            # If cache is empty, load from files
            if not components:
                plist_pattern = os.path.join(STATUS_PATH, '*.avc.plist')
                for plist_file in glob.glob(plist_pattern):
                    load_plist_to_cache(plist_file)
                components = STATE_CACHE.get_components()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'components': components}))

        except Exception as e:
            print "State query error:", e
            self.send_error(500, str(e))

    def handle_command(self):
        try:
            length = int(self.headers.getheader('content-length'))
            body = self.rfile.read(length)

            # Parse JSON command
            # Expected format:
            # {
            #   "zone": "Family Room",
            #   "component": "Lutron",
            #   "logicalComponent": "Lighting_controller",
            #   "service": "SVC_ENV_LIGHTING",
            #   "serviceVariantID": "1",
            #   "command": "AllLightsOn"
            # }
            try:
                cmd = json.loads(body)
            except:
                self.send_error(400, "Invalid JSON")
                return

            zone = cmd.get('zone', '')
            component = cmd.get('component', '')
            logical = cmd.get('logicalComponent', '')
            service = cmd.get('service', '')
            variant = cmd.get('serviceVariantID', '1')
            command = cmd.get('command', '')
            arguments = cmd.get('arguments', {})

            if not all([zone, component, logical, service, command]):
                self.send_error(400, "Missing required fields")
                return

            # Build args XML
            args_xml = ''
            for name, value in arguments.items():
                args_xml += '<arg name="%s" value="%s"/>' % (name, value)

            print "Sending command: %s - %s - %s (args: %s)" % (zone, service, command, arguments)
            print "Args XML: %s" % args_xml

            # Build SOAP message
            soap = SOAP_TEMPLATE.format(
                zone=zone,
                component=component,
                logical=logical,
                service=service,
                variant=variant,
                command=command,
                args=args_xml
            )

            # Send via UDP to UIS
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5.0)
            try:
                sock.sendto(soap.encode('utf-8'), (SAVANT_HOST, SAVANT_UIS_PORT))
                print "SOAP sent to %s:%d" % (SAVANT_HOST, SAVANT_UIS_PORT)
            except Exception as e:
                print "Send error:", e
                self.send_error(502, "Send error: " + str(e))
                return
            finally:
                sock.close()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}))

        except Exception as e:
            print "Command failed:", e
            self.send_error(500, str(e))

def main():
    # Start background threads for state watching
    plist_thread = threading.Thread(target=plist_watcher_thread)
    plist_thread.daemon = True
    plist_thread.start()

    # Only start Lutron listener if enabled (disabled by default to avoid
    # blocking Savant's connection - Lutron only supports limited connections)
    if LUTRON_PERSISTENT:
        lutron_thread = threading.Thread(target=lutron_listener_thread)
        lutron_thread.daemon = True
        lutron_thread.start()
    else:
        print "Lutron persistent listener disabled (LUTRON_PERSISTENT=False)"

    # Give threads a moment to initialize
    time.sleep(1)

    # Start HTTP server
    server_address = ('0.0.0.0', LISTEN_PORT)
    httpd = BaseHTTPServer.HTTPServer(server_address, SavantRequestHandler)
    print "Savant REST Relay listening on port", LISTEN_PORT
    httpd.serve_forever()

if __name__ == "__main__":
    main()
