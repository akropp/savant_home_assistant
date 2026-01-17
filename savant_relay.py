#!/usr/bin/env python
import BaseHTTPServer
import json
import sqlite3
import socket
import subprocess
import sys
import re

# Config
DB_PATH = '/home/RPM/GNUstep/Library/ApplicationSupport/RacePointMedia/serviceImplementation.sqlite'
SAVANT_HOST = '127.0.0.1'
LISTEN_PORT = 8081

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
    server_address = ('0.0.0.0', LISTEN_PORT)
    httpd = BaseHTTPServer.HTTPServer(server_address, SavantRequestHandler)
    print "Savant REST Relay listening on port", LISTEN_PORT
    httpd.serve_forever()

if __name__ == "__main__":
    main()
