#!/usr/bin/env python
import BaseHTTPServer
import json
import sqlite3
import socket
import sys

# Config
DB_PATH = '/home/RPM/GNUstep/Library/ApplicationSupport/RacePointMedia/serviceImplementation.sqlite'
SAVANT_HOST = '127.0.0.1'
SAVANT_UIS_PORT = 33562  # UDP SOAP port
LISTEN_PORT = 8081

# SOAP template for service commands
SOAP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:SOAP-ENC="http://www.w3.org/2003/05/soap-encoding" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:wsdl="http://tempuri.org/wsdl.xsd" xmlns:md="urn:rpm-metadatainterface" xmlns:ctl="urn:rpm-controlinterface" xmlns:rdm="urn:rpm-rdminterface" xmlns:rpm="urn:rpm-common" xmlns:sm="urn:rpm-stateManagementInterface" xmlns:smrdm="urn:sm-rdminterface" xmlns:snsr="urn:rpm-userSNSRInterface" xmlns:sync="urn:rpm-syncinterface"><SOAP-ENV:Body><ctl:serviceEventRequest><zoneString>{zone}</zoneString><componentString>{component}</componentString><logicalComponentString>{logical}</logicalComponentString><serviceString>{service}</serviceString><serviceVariantIDString>{variant}</serviceVariantIDString><commandString>{command}</commandString></ctl:serviceEventRequest></SOAP-ENV:Body></SOAP-ENV:Envelope>"""

class SavantRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/zones':
            self.handle_zones()
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

            if not all([zone, component, logical, service, command]):
                self.send_error(400, "Missing required fields")
                return

            print "Sending command: %s - %s - %s" % (zone, service, command)

            # Build SOAP message
            soap = SOAP_TEMPLATE.format(
                zone=zone,
                component=component,
                logical=logical,
                service=service,
                variant=variant,
                command=command
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
