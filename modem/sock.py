# -*- coding: utf-8 -*-
"""
Created on Fri Dec 25 21:25:14 2020

@author: DJ2LS

# GET COMMANDS
    # "command" : "..."

    # SET COMMANDS
    # "command" : "..."
    # "parameter" : " ..."

    # DATA COMMANDS
    # "command" : "..."
    # "type" : "..."
    # "dxcallsign" : "..."
    # "data" : "..."
"""
import atexit
import base64
import queue
import socketserver
import sys
import threading
import time
import wave
import helpers
import static
from global_instances import ARQ, AudioParam, Beacon, Channel, Daemon, HamlibParam, ModemParam, Station, Statistics, TCIParam, Modem, MeshParam
import structlog
from random import randrange
import ujson as json
from exceptions import NoCallsign
from queues import DATA_QUEUE_TRANSMIT, RX_BUFFER, RIGCTLD_COMMAND_QUEUE, MESH_QUEUE_TRANSMIT, MESH_SIGNALLING_TABLE

SOCKET_QUEUE = queue.Queue()
DAEMON_QUEUE = queue.Queue()

CONNECTED_CLIENTS = set()
CLOSE_SIGNAL = False

TESTMODE = False

log = structlog.get_logger("sock")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """
    the socket handler base class
    """

    pass


# noinspection PyTypeChecker
class ThreadedTCPRequestHandler(socketserver.StreamRequestHandler):
    """ """
    connection_alive = False
    log = structlog.get_logger("ThreadedTCPRequestHandler")

    def send_to_client(self):
        """
        function called by socket handler
        send data to a network client if available
        """
        tempdata = b""
        while self.connection_alive and not CLOSE_SIGNAL:
            # send modem state as network stream
            # check server port against daemon port and send corresponding data
            if self.server.server_address[1] == Modem.port and not Daemon.modemstarted:
                data = send_modem_state()
                if data != tempdata:
                    tempdata = data
                    SOCKET_QUEUE.put(data)
            else:
                data = send_daemon_state()
                if data != tempdata:
                    tempdata = data
                    SOCKET_QUEUE.put(data)
                threading.Event().wait(0.5)

            while not SOCKET_QUEUE.empty():

                try:

                    data = SOCKET_QUEUE.get()
                    sock_data = bytes(data, "utf-8")
                    sock_data += b"\n"  # append line limiter

                    # send data to all connected clients
                    for client in CONNECTED_CLIENTS:
                        try:
                            client.send(sock_data)
                        except Exception as err:
                            self.log.info("[SCK] Connection lost", e=err)

                            try:
                                self.log.warning("[SCK] removing client from sock", client=client, set=CONNECTED_CLIENTS)
                                CONNECTED_CLIENTS.remove(client)
                            except Exception as sockerr:
                                self.log.warning("[SCK] Err remove client from CONNECTED_CLIENTS", e=sockerr, client=client, set=CONNECTED_CLIENTS)
                                self.log.info("[SCK] resetting sock")

                                # TODO Check if we really should set connection alive to false.
                                # This might disconnect all other clients as well...
                                self.connection_alive = False

                except Exception as err:
                    self.log.debug("[SCK] err while sending data to sock", e=err)

            # we want to transmit scatter data only once to reduce network traffic
            ModemParam.scatter = []
            # self.request.sendall(sock_data)
            threading.Event().wait(0.15)

    def receive_from_client(self):
        """
        function which is called by the socket handler
        it processes the data which is returned by a client
        """
        data = bytes()
        while self.connection_alive and not CLOSE_SIGNAL:
            try:
                chunk = self.request.recv(1024)
                data += chunk

                if chunk == b"":
                    # print("connection broken. Closing...")
                    self.connection_alive = False

                if data.startswith(b"{") and data.endswith(b"}\n"):
                    # split data by \n if we have multiple commands in socket buffer
                    data = data.split(b"\n")
                    # remove empty data
                    data.remove(b"")

                    # iterate thorugh data list
                    for commands in data:
                        if self.server.server_address[1] == Modem.port:
                            self.process_modem_commands(commands)
                        else:
                            self.process_daemon_commands(commands)

                        # wait some time between processing multiple commands
                        # this is only a first test to avoid doubled transmission
                        # we might improve this by only processing one command or
                        # doing some kind of selection to determine which commands need to be dropped
                        # and which one can be processed during a running transmission
                        threading.Event().wait(0.5)

                    # finally delete our rx buffer to be ready for new commands
                    data = bytes()
            except Exception as err:
                self.log.info(
                    "[SCK] Connection closed",
                    ip=self.client_address[0],
                    port=self.client_address[1],
                    e=err,
                )
                self.connection_alive = False

    def handle(self):
        """
        socket handler
        """
        CONNECTED_CLIENTS.add(self.request)

        self.log.debug(
            "[SCK] Client connected",
            ip=self.client_address[0],
            port=self.client_address[1],
        )
        self.connection_alive = True

        self.sendThread = threading.Thread(
            target=self.send_to_client, args=[], daemon=True
        )
        self.sendThread.start()
        self.receiveThread = threading.Thread(
            target=self.receive_from_client, args=[], daemon=True
        )
        self.receiveThread.start()

        # keep connection alive until we close it
        while self.connection_alive and not CLOSE_SIGNAL:
            threading.Event().wait(1)

    def finish(self):
        """ """
        self.log.warning(
            "[SCK] Closing client socket",
            ip=self.client_address[0],
            port=self.client_address[1],
        )
        try:
            CONNECTED_CLIENTS.remove(self.request)
        except Exception as e:
            self.log.warning(
                "[SCK] client connection already removed from client list",
                client=self.request,
                e=e,
            )

    # ------------------------ Modem COMMANDS
    def process_modem_commands(self, data):
        """
        process modem commands

        Args:
          data:

        Returns:

        """
        log = structlog.get_logger("process_modem_commands")

        # we need to do some error handling in case of socket timeout or decoding issue
        try:
            # convert data to json object
            received_json = json.loads(data)
            log.debug("[SCK] CMD", command=received_json)

            # ENABLE Modem LISTENING STATE
            if received_json["type"] == "set" and received_json["command"] == "listen":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_set_listen(None, received_json)
                else:
                    self.modem_set_listen(received_json)

            # START STOP AUDIO RECORDING
            if received_json["type"] == "set" and received_json["command"] == "record_audio":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_set_record_audio(None, received_json)
                else:
                    self.modem_set_record_audio(received_json)

            # SET ENABLE/DISABLE RESPOND TO CALL
            if received_json["type"] == "set" and received_json["command"] == "respond_to_call":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_set_respond_to_call(None, received_json)
                else:
                    self.modem_set_respond_to_call(received_json)

            # SET ENABLE RESPOND TO CQ
            if received_json["type"] == "set" and received_json["command"] == "respond_to_cq":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_set_record_audio(None, received_json)
                else:
                    self.modem_set_record_audio(received_json)

            # SET TX AUDIO LEVEL
            if received_json["type"] == "set" and received_json["command"] == "tx_audio_level":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_set_tx_audio_level(None, received_json)
                else:
                    self.modem_set_tx_audio_level(received_json)


            # TRANSMIT TEST FRAME
            if received_json["type"] == "set" and received_json["command"] == "send_test_frame":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_set_send_test_frame(None, received_json)
                elif Modem.modem_state in ['busy']:
                    log.warning(
                        "[SCK] Dropping command",
                        e="modem state",
                        state=Modem.modem_state,
                        command=received_json,
                    )
                else:
                    self.modem_set_send_test_frame(received_json)

            # TRANSMIT FEC FRAME
            if received_json["type"] == "fec" and received_json["command"] == "transmit":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_fec_transmit(None, received_json)
                else:
                    self.modem_fec_transmit(received_json)

            # TRANSMIT IS WRITING FRAME
            if received_json["type"] == "fec" and received_json["command"] == "transmit_is_writing":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_fec_is_writing(None, received_json)
                elif Modem.modem_state in ['busy']:
                    log.warning(
                        "[SCK] Dropping command",
                        e="modem state",
                        state=Modem.modem_state,
                        command=received_json,
                    )
                else:
                    self.modem_fec_is_writing(received_json)

            # CQ CQ CQ
            if received_json["command"] == "cqcqcq":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_cqcqcq(None, received_json)
                elif Modem.modem_state in ['BUSY']:
                    log.warning(
                        "[SCK] Dropping command",
                        e="modem state",
                        state=Modem.modem_state,
                        command=received_json,
                    )
                else:
                    self.modem_cqcqcq(received_json)

            # START_BEACON
            if received_json["command"] == "start_beacon":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_start_beacon(None, received_json)
                else:
                    self.modem_start_beacon(received_json)

            # STOP_BEACON
            if received_json["command"] == "stop_beacon":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_stop_beacon(None, received_json)
                else:
                    self.modem_stop_beacon(received_json)

            # PING
            if received_json["type"] == "ping" and received_json["command"] == "ping":

                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_ping_ping(None, received_json)
                elif Modem.modem_state in ['BUSY']:
                    log.warning(
                        "[SCK] Dropping command",
                        e="modem state",
                        state=Modem.modem_state,
                        command=received_json,
                    )

                else:
                    self.modem_ping_ping(received_json)

            # CONNECT
            if received_json["type"] == "arq" and received_json["command"] == "connect":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_arq_connect(None, received_json)
                elif Modem.modem_state in ['BUSY']:
                    log.warning(
                        "[SCK] Dropping command",
                        e="modem state",
                        state=Modem.modem_state,
                        command=received_json,
                    )
                else:
                    self.modem_arq_connect(received_json)

            # DISCONNECT
            if received_json["type"] == "arq" and received_json["command"] == "disconnect":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_arq_disconnect(None, received_json)
                else:
                    self.modem_arq_disconnect(received_json)

            # TRANSMIT RAW DATA
            if received_json["type"] == "arq" and received_json["command"] == "send_raw":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_arq_send_raw(None, received_json)
                elif Modem.modem_state in ['busy']:
                    log.warning(
                        "[SCK] Dropping command",
                        e="modem state",
                        state=Modem.modem_state,
                        command=received_json,
                    )
                else:
                    self.modem_arq_send_raw(received_json)

            # STOP TRANSMISSION
            if received_json["type"] == "arq" and received_json["command"] == "stop_transmission":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_arq_stop_transmission(None, received_json)
                else:
                    self.modem_arq_stop_transmission(received_json)

            # GET RX BUFFER
            if received_json["type"] == "get" and received_json["command"] == "rx_buffer":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_get_rx_buffer(None, received_json)
                else:
                    self.modem_get_rx_buffer(received_json)

            # DELETE RX BUFFER
            if received_json["type"] == "set" and received_json["command"] == "del_rx_buffer":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_set_del_rx_buffer(None, received_json)
                else:
                    self.modem_set_del_rx_buffer(received_json)
            # SET FREQUENCY
            if received_json["type"] == "set" and received_json["command"] == "frequency":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_set_frequency(None, received_json)
                else:
                    self.modem_set_frequency(received_json)

            # SET MODE
            if received_json["type"] == "set" and received_json["command"] == "mode":
                if TESTMODE:
                    ThreadedTCPRequestHandler.modem_set_mode(None, received_json)
                else:
                    self.modem_set_mode(received_json)

            # GET ROUTING TABLE
            if received_json["type"] == "get" and received_json["command"] == "routing_table":
                self.modem_get_mesh_routing_table(received_json)


            # -------------- MESH ---------------- #
            # MESH PING
            if received_json["type"] == "mesh" and received_json["command"] == "ping":
                self.modem_mesh_ping(received_json)

        except Exception as err:
            log.error("[SCK] JSON decoding error", e=err)

    def modem_set_listen(self, received_json):
        try:
            Modem.listen = received_json["state"] in ['true', 'True', True, "ON", "on"]
            command_response("listen", True)

            # if modem is connected, force disconnect when Modem.listen == False
            if not Modem.listen and ARQ.arq_session_state not in ["disconnecting", "disconnected", "failed"]:
                DATA_QUEUE_TRANSMIT.put(["DISCONNECT"])
                # set early disconnecting state so we can interrupt connection attempts
                ARQ.arq_session_state = "disconnecting"
                command_response("disconnect", True)



        except Exception as err:
            command_response("listen", False)
            log.warning(
                "[SCK] CQ command execution error", e=err, command=received_json
            )

    def modem_set_record_audio(self, received_json):
        try:
            if not AudioParam.audio_record:
                AudioParam.audio_record_file = wave.open(f"{int(time.time())}_audio_recording.wav", 'w')
                AudioParam.audio_record_file.semodemhannels(1)
                AudioParam.audio_record_file.setsampwidth(2)
                AudioParam.audio_record_file.setframerate(8000)
                AudioParam.audio_record = True
            else:
                AudioParam.audio_record = False
                AudioParam.audio_record_file.close()

            command_response("respond_to_call", True)

        except Exception as err:
            command_response("respond_to_call", False)
            log.warning(
                "[SCK] CQ command execution error", e=err, command=received_json
            )

    def modem_set_respond_to_call(self, received_json):
        try:
            Modem.respond_to_call = received_json["state"] in ['true', 'True', True]
            command_response("respond_to_call", True)

        except Exception as err:
            command_response("respond_to_call", False)
            log.warning(
                "[SCK] CQ command execution error", e=err, command=received_json
            )

    def modem_set_respond_to_cq(self, received_json):
        try:
            Modem.respond_to_cq = received_json["state"] in ['true', 'True', True]
            command_response("respond_to_cq", True)

        except Exception as err:
            command_response("respond_to_cq", False)
            log.warning(
                "[SCK] CQ command execution error", e=err, command=received_json
            )

    def modem_set_tx_audio_level(self, received_json):
        try:
            AudioParam.tx_audio_level = int(received_json["value"])
            command_response("tx_audio_level", True)

        except Exception as err:
            command_response("tx_audio_level", False)
            log.warning(
                "[SCK] TX audio command execution error",
                e=err,
                command=received_json,
            )

    def modem_set_send_test_frame(self, received_json):
        try:
            DATA_QUEUE_TRANSMIT.put(["SEND_TEST_FRAME"])
            command_response("send_test_frame", True)
        except Exception as err:
            command_response("send_test_frame", False)
            log.warning(
                "[SCK] Send test frame command execution error",
                e=err,
                command=received_json,
            )

    def modem_fec_transmit(self, received_json):
        try:
            mode = received_json["mode"]
            wakeup = received_json["wakeup"]
            base64data = received_json["payload"]
            if len(base64data) % 4:
                raise TypeError
            payload = base64.b64decode(base64data)

            try:
                mycallsign = received_json["mycallsign"]
                mycallsign = helpers.callsign_to_bytes(mycallsign)
                mycallsign = helpers.bytes_to_callsign(mycallsign)

            except Exception:
                mycallsign = Station.mycallsign


            DATA_QUEUE_TRANSMIT.put(["FEC", mode, wakeup, payload, mycallsign])
            command_response("fec_transmit", True)
        except Exception as err:
            command_response("fec_transmit", False)
            log.warning(
                "[SCK] Send fec frame command execution error",
                e=err,
                command=received_json,
            )

    def modem_fec_is_writing(self, received_json):
        try:
            mycallsign = received_json["mycallsign"]
            DATA_QUEUE_TRANSMIT.put(["FEC_IS_WRITING", mycallsign])
            command_response("fec_is_writing", True)
        except Exception as err:
            command_response("fec_is_writing", False)
            log.warning(
                "[SCK] Send fec frame command execution error",
                e=err,
                command=received_json,
            )

    def modem_cqcqcq(self, received_json):
        try:
            DATA_QUEUE_TRANSMIT.put(["CQ"])
            command_response("cqcqcq", True)

        except Exception as err:
            command_response("cqcqcq", False)
            log.warning(
                "[SCK] CQ command execution error", e=err, command=received_json
            )

    def modem_start_beacon(self, received_json):
        try:
            Beacon.beacon_state = True
            interval = int(received_json["parameter"])
            DATA_QUEUE_TRANSMIT.put(["BEACON", interval, True])
            command_response("start_beacon", True)
        except Exception as err:
            command_response("start_beacon", False)
            log.warning(
                "[SCK] Start beacon command execution error",
                e=err,
                command=received_json,
            )

    def modem_stop_beacon(self, received_json):
        try:
            log.warning("[SCK] Stopping beacon!")
            Beacon.beacon_state = False
            DATA_QUEUE_TRANSMIT.put(["BEACON", None, False])
            command_response("stop_beacon", True)
        except Exception as err:
            command_response("stop_beacon", False)
            log.warning(
                "[SCK] Stop beacon command execution error",
                e=err,
                command=received_json,
            )


    def modem_mesh_ping(self, received_json):
        # send ping frame and wait for ACK
        try:
            dxcallsign = received_json["dxcallsign"]
            if not str(dxcallsign).strip():
                raise NoCallsign

            # additional step for being sure our callsign is correctly
            # in case we are not getting a station ssid
            # then we are forcing a station ssid = 0
            dxcallsign = helpers.callsign_to_bytes(dxcallsign)
            dxcallsign = helpers.bytes_to_callsign(dxcallsign)

            # check if specific callsign is set with different SSID than the Modem is initialized
            try:
                mycallsign = received_json["mycallsign"]
                mycallsign = helpers.callsign_to_bytes(mycallsign)
                mycallsign = helpers.bytes_to_callsign(mycallsign)

            except Exception:
                mycallsign = Station.mycallsign

            MESH_QUEUE_TRANSMIT.put(["PING", mycallsign, dxcallsign])
            command_response("ping", True)
        except NoCallsign:
            command_response("ping", False)
            log.warning("[SCK] callsign required for ping", command=received_json)
        except Exception as err:
            command_response("ping", False)
            log.warning(
                "[SCK] PING command execution error", e=err, command=received_json
            )

    def modem_ping_ping(self, received_json):
        # send ping frame and wait for ACK

        try:
            dxcallsign = received_json["dxcallsign"]
            if not str(dxcallsign).strip():
                raise NoCallsign

            # additional step for being sure our callsign is correctly
            # in case we are not getting a station ssid
            # then we are forcing a station ssid = 0
            dxcallsign = helpers.callsign_to_bytes(dxcallsign)
            dxcallsign = helpers.bytes_to_callsign(dxcallsign)

            # check if specific callsign is set with different SSID than the Modem is initialized
            try:
                mycallsign = received_json["mycallsign"]
                mycallsign = helpers.callsign_to_bytes(mycallsign)
                mycallsign = helpers.bytes_to_callsign(mycallsign)

            except Exception:
                mycallsign = Station.mycallsign

            DATA_QUEUE_TRANSMIT.put(["PING", mycallsign, dxcallsign])
            command_response("ping", True)
        except NoCallsign:
            command_response("ping", False)
            log.warning("[SCK] callsign required for ping", command=received_json)
        except Exception as err:
            command_response("ping", False)
            log.warning(
                "[SCK] PING command execution error", e=err, command=received_json
            )

    def modem_arq_connect(self, received_json):

        # pause our beacon first
        Beacon.beacon_pause = True

        # check for connection attempts key
        try:
            attempts = int(received_json["attempts"])
        except Exception:
            # 15 == self.session_connect_max_retries
            attempts = 15

        dxcallsign = received_json["dxcallsign"]

        # check if specific callsign is set with different SSID than the Modem is initialized
        try:
            mycallsign = received_json["mycallsign"]
            mycallsign = helpers.callsign_to_bytes(mycallsign)
            mycallsign = helpers.bytes_to_callsign(mycallsign)

        except Exception:
            mycallsign = Station.mycallsign

        # additional step for being sure our callsign is correctly
        # in case we are not getting a station ssid
        # then we are forcing a station ssid = 0
        dxcallsign = helpers.callsign_to_bytes(dxcallsign)
        dxcallsign = helpers.bytes_to_callsign(dxcallsign)

        if ARQ.arq_session_state not in ["disconnected", "failed"]:
            command_response("connect", False)
            log.warning(
                "[SCK] Connect command execution error",
                e=f"already connected to station:{Station.dxcallsign}",
                command=received_json,
            )
        else:

            # finally check again if we are disconnected or failed

            # try connecting
            try:

                DATA_QUEUE_TRANSMIT.put(["CONNECT", mycallsign, dxcallsign, attempts])
                command_response("connect", True)
            except Exception as err:
                command_response("connect", False)
                log.warning(
                    "[SCK] Connect command execution error",
                    e=err,
                    command=received_json,
                )
                # allow beacon transmission again
                Beacon.beacon_pause = False

            # allow beacon transmission again
            Beacon.beacon_pause = False

    def modem_arq_disconnect(self, received_json):
        try:
            if ARQ.arq_session_state not in ["disconnecting", "disconnected", "failed"]:
                DATA_QUEUE_TRANSMIT.put(["DISCONNECT"])

                # set early disconnecting state so we can interrupt connection attempts
                ARQ.arq_session_state = "disconnecting"
                command_response("disconnect", True)
            else:
                command_response("disconnect", False)

        except Exception as err:
            command_response("disconnect", False)
            log.warning(
                "[SCK] Disconnect command execution error",
                e=err,
                command=received_json,
            )

    def modem_arq_send_raw(self, received_json):
        Beacon.beacon_pause = True

        # wait some random time
        helpers.wait(randrange(5, 25, 5) / 10.0)

        # TODO carefully test this
        # avoid sending data while we are receiving codec2 signalling data
        interrupt_time = time.time() + 5
        while ModemParam.is_codec2_traffic and time.time() < interrupt_time:
            threading.Event().wait(0.01)

        # we need to warn if already in arq state
        if ARQ.arq_state:
            command_response("send_raw", False)
            log.warning(
                "[SCK] Send raw command execution warning",
                e="already in arq state",
                i="command queued",
                command=received_json,
            )

        try:
            if not ARQ.arq_session:
                dxcallsign = received_json["parameter"][0]["dxcallsign"]
                # additional step for being sure our callsign is correctly
                # in case we are not getting a station ssid
                # then we are forcing a station ssid = 0
                dxcallsign = helpers.callsign_to_bytes(dxcallsign)
                dxcallsign = helpers.bytes_to_callsign(dxcallsign)

                command_response("send_raw", True)
            else:
                dxcallsign = Station.dxcallsign
                Station.dxcallsign_crc = helpers.get_crc_24(Station.dxcallsign)

            base64data = received_json["parameter"][0]["data"]

            # check if specific callsign is set with different SSID than the Modem is initialized
            try:
                mycallsign = received_json["parameter"][0]["mycallsign"]
                mycallsign = helpers.callsign_to_bytes(mycallsign)
                mycallsign = helpers.bytes_to_callsign(mycallsign)

            except Exception:
                mycallsign = Station.mycallsign

            # check for connection attempts key
            try:
                attempts = int(received_json["parameter"][0]["attempts"])

            except Exception:
                attempts = 10

            # check if transmission uuid provided else set no-uuid
            try:
                arq_uuid = received_json["uuid"]
            except Exception:
                arq_uuid = "no-uuid"

            if len(base64data) % 4:
                raise TypeError

            binarydata = base64.b64decode(base64data)
            # check if hmac hash is provided
            try:
                log.info("[SCK] [HMAC] Looking for salt/token", local=mycallsign, remote=dxcallsign)
                hmac_salt = helpers.get_hmac_salt(dxcallsign, mycallsign)
                log.info("[SCK] [HMAC] Salt info", local=mycallsign, remote=dxcallsign, salt=hmac_salt)
            except Exception:
                log.warning("[SCK] [HMAC] No salt/token found")
                hmac_salt = ''
            DATA_QUEUE_TRANSMIT.put(
                ["ARQ_RAW", binarydata, arq_uuid, mycallsign, dxcallsign, attempts, hmac_salt]
            )

        except Exception as err:
            command_response("send_raw", False)
            log.warning(
                "[SCK] Send raw command execution error",
                e=err,
                command=received_json,
            )

    def modem_arq_stop_transmission(self, received_json):
        try:
            if Modem.modem_state == "BUSY" or ARQ.arq_state:
                DATA_QUEUE_TRANSMIT.put(["STOP"])
            log.warning("[SCK] Stopping transmission!")
            Modem.modem_state = "IDLE"
            ARQ.arq_state = False
            command_response("stop_transmission", True)
        except Exception as err:
            command_response("stop_transmission", False)
            log.warning(
                "[SCK] STOP command execution error", e=err, command=received_json
            )

    def modem_get_mesh_routing_table(self, received_json):
        try:
            output = {
                "command": "routing_table",
                "routes": [],
            }

            for _, route in enumerate(MeshParam.routing_table):
                if MeshParam.routing_table[_][0].hex() == helpers.get_crc_24(b"direct").hex():
                    router = "direct"
                else:
                    router = MeshParam.routing_table[_][0].hex()
                output["routes"].append(
                    {
                        "dxcall": MeshParam.routing_table[_][0].hex(),
                        "router": router,
                        "hops": MeshParam.routing_table[_][2],
                        "snr": MeshParam.routing_table[_][3],
                        "score": MeshParam.routing_table[_][4],
                        "timestamp": MeshParam.routing_table[_][5],
                    }
                )


            jsondata = json.dumps(output)
            # self.request.sendall(bytes(jsondata, encoding))
            SOCKET_QUEUE.put(jsondata)
            command_response("routing_table", True)

        except Exception as err:
            command_response("routing_table", False)
            log.warning(
                "[SCK] Send RX buffer command execution error",
                e=err,
                command=received_json,
            )

    def modem_get_rx_buffer(self, received_json):
        try:
            if not RX_BUFFER.empty():
                # TODO REMOVE DEPRECATED MESSAGES
                #output = {
                #    "command": "rx_buffer",
                #    "data-array": [],
                #}#

                #for _buffer_length in range(RX_BUFFER.qsize()):
                #    base64_data = RX_BUFFER.queue[_buffer_length][4]
                #    output["data-array"].append(
                #        {
                #            "uuid": RX_BUFFER.queue[_buffer_length][0],
                #            "timestamp": RX_BUFFER.queue[_buffer_length][1],
                #            "dxcallsign": str(RX_BUFFER.queue[_buffer_length][2], "utf-8"),
                #            "dxgrid": str(RX_BUFFER.queue[_buffer_length][3], "utf-8"),
                #            "data": base64_data,
                #        }
                #    )
                #jsondata = json.dumps(output)
                ## self.request.sendall(bytes(jsondata, encoding))
                #SOCKET_QUEUE.put(jsondata)
                #command_response("rx_buffer", True)


                # REQUEST REQUEST RX BUFFER AGAIN
                # NEW BEHAVIOUR IS, PUSHING DATA TO NETWORK LIKE WE RECEIVED IT
                #  RX_BUFFER[0] = transmission uuid
                #  RX_BUFFER[1] = timestamp
                #  RX_BUFFER[2] = dxcallsign
                #  RX_BUFFER[3] = dxgrid
                #  RX_BUFFER[4] = data
                #  RX_BUFFER[5] = hmac signed
                #  RX_BUFFER[6] = compression factor
                #  RX_BUFFER[7] = bytes per minute
                #  RX_BUFFER[8] = duration
                #  RX_BUFFER[9] = self.frame_nack_counter
                #  RX_BUFFER[10] = speed list stats
                for _buffer_length in range(RX_BUFFER.qsize()):
                    output = {
                        "freedata" : "modem-message",
                        "arq" : "transmission",
                        "status" : "received",
                        "uuid" : RX_BUFFER.queue[_buffer_length][0],
                        "percent" : 100,
                        "bytesperminute" : RX_BUFFER.queue[_buffer_length][7],
                        "compression" : RX_BUFFER.queue[_buffer_length][6],
                        "timestamp" : RX_BUFFER.queue[_buffer_length][1],
                        "finished" : 0,
                        "mycallsign" : str(Station.mycallsign, "UTF-8"),
                        "dxcallsign" : str(RX_BUFFER.queue[_buffer_length][2], "utf-8"),
                        "dxgrid" : str(RX_BUFFER.queue[_buffer_length][3], "utf-8"),
                        "data" : RX_BUFFER.queue[_buffer_length][4],
                        "irs" : RX_BUFFER.queue[_buffer_length][5],
                        "hmac_signed" : "False",
                        "duration" : RX_BUFFER.queue[_buffer_length][8],
                        "nacks" : RX_BUFFER.queue[_buffer_length][9],
                        "speed_list" : RX_BUFFER.queue[_buffer_length][10]
                    }

                    jsondata = json.dumps(output)
                    SOCKET_QUEUE.put(jsondata)
                    print(jsondata)

                    command_response("rx_buffer", True)




        except Exception as err:
            command_response("rx_buffer", False)
            log.warning(
                "[SCK] Send RX buffer command execution error",
                e=err,
                command=received_json,
            )

    def modem_set_del_rx_buffer(self, received_json):
        try:
            RX_BUFFER.queue.clear()
            command_response("del_rx_buffer", True)
        except Exception as err:
            command_response("del_rx_buffer", False)
            log.warning(
                "[SCK] Delete RX buffer command execution error",
                e=err,
                command=received_json,
            )

    def modem_set_mode(self, received_json):
        try:
            RIGCTLD_COMMAND_QUEUE.put(["set_mode", received_json["mode"]])
            command_response("set_mode", True)
        except Exception as err:
            command_response("set_mode", False)
            log.warning(
                "[SCK] Set mode command execution error",
                e=err,
                command=received_json,
            )

    def modem_set_frequency(self, received_json):
        try:
            RIGCTLD_COMMAND_QUEUE.put(["set_frequency", received_json["frequency"]])
            command_response("set_frequency", True)
        except Exception as err:
            command_response("set_frequency", False)
            log.warning(
                "[SCK] Set frequency command execution error",
                e=err,
                command=received_json,
            )

    # ------------------------ DAEMON COMMANDS
    def process_daemon_commands(self, data):
        """
        process daemon commands

        Args:
          data:

        Returns:

        """
        log = structlog.get_logger("process_daemon_commands")

        # convert data to json object
        received_json = json.loads(data)
        log.debug("[SCK] CMD", command=received_json)

        if received_json["type"] == "set" and received_json["command"] == "mycallsign":
            self.daemon_set_mycallsign(received_json)

        if received_json["type"] == "set" and received_json["command"] == "mygrid":
            self.daemon_set_mygrid(received_json)

        if (
                received_json["type"] == "set"
                and received_json["command"] == "start_modem"
                #  and not Daemon.modemstarted
        ):
            self.daemon_start_modem(received_json)

        if received_json["type"] == "get" and received_json["command"] == "test_hamlib":
            self.daemon_test_hamlib(received_json)

        if received_json["type"] == "set" and received_json["command"] == "stop_modem":
            self.daemon_stop_modem(received_json)

        if received_json["type"] == "set" and received_json["command"] == "start_rigctld" and not Daemon.rigctldstarted:
            self.daemon_start_rigctld(received_json)

        if received_json["type"] == "set" and received_json["command"] == "stop_rigctld":
            self.daemon_stop_rigctld(received_json)

    def daemon_set_mycallsign(self, received_json):
        try:
            callsign = received_json["parameter"]

            if bytes(callsign, "utf-8") == b"":
                self.request.sendall(b"INVALID CALLSIGN")
                log.warning(
                    "[SCK] SET MYCALL FAILED",
                    call=Station.mycallsign,
                    crc=Station.mycallsign_crc.hex(),
                )
            else:
                Station.mycallsign = bytes(callsign, "utf-8")
                Station.mycallsign_crc = helpers.get_crc_24(Station.mycallsign)

                command_response("mycallsign", True)
                log.info(
                    "[SCK] SET MYCALL",
                    call=Station.mycallsign,
                    crc=Station.mycallsign_crc.hex(),
                )
        except Exception as err:
            command_response("mycallsign", False)
            log.warning("[SCK] command execution error", e=err, command=received_json)

    def daemon_set_mygrid(self, received_json):
        try:
            mygrid = received_json["parameter"]

            if bytes(mygrid, "utf-8") == b"":
                self.request.sendall(b"INVALID GRID")
                command_response("mygrid", False)
            else:
                Station.mygrid = bytes(mygrid, "utf-8")
                log.info("[SCK] SET MYGRID", grid=Station.mygrid)
                command_response("mygrid", True)
        except Exception as err:
            command_response("mygrid", False)
            log.warning("[SCK] command execution error", e=err, command=received_json)

    def daemon_start_modem(self, received_json):
        try:
            startparam = received_json["parameter"][0]

            mycall = str(helpers.return_key_from_object("AA0AA", startparam, "mycall"))
            mygrid = str(helpers.return_key_from_object("JN12ab", startparam, "mygrid"))
            rx_audio = str(helpers.return_key_from_object("0", startparam, "rx_audio"))
            tx_audio = str(helpers.return_key_from_object("0", startparam, "tx_audio"))
            radiocontrol = str(helpers.return_key_from_object("disabled", startparam, "radiocontrol"))
            rigctld_ip = str(helpers.return_key_from_object("127.0.0.1", startparam, "rigctld_ip"))
            rigctld_port = str(helpers.return_key_from_object("4532", startparam, "rigctld_port"))
            enable_scatter = str(helpers.return_key_from_object("True", startparam, "enable_scatter"))
            enable_fft = str(helpers.return_key_from_object("True", startparam, "enable_fft"))
            enable_fsk = str(helpers.return_key_from_object("False", startparam, "enable_fsk"))
            low_bandwidth_mode = str(helpers.return_key_from_object("False", startparam, "low_bandwidth_mode"))
            tuning_range_fmin = str(helpers.return_key_from_object("-50", startparam, "tuning_range_fmin"))
            tuning_range_fmax = str(helpers.return_key_from_object("50", startparam, "tuning_range_fmax"))
            tx_audio_level = str(helpers.return_key_from_object("100", startparam, "tx_audio_level"))
            respond_to_cq = str(helpers.return_key_from_object("False", startparam, "respond_to_cq"))
            rx_buffer_size = str(helpers.return_key_from_object("16", startparam, "rx_buffer_size"))
            enable_explorer = str(helpers.return_key_from_object("False", startparam, "enable_explorer"))
            enable_auto_tune = str(helpers.return_key_from_object("False", startparam, "enable_auto_tune"))
            enable_stats = str(helpers.return_key_from_object("False", startparam, "enable_stats"))
            tx_delay = str(helpers.return_key_from_object("0", startparam, "tx_delay"))
            tci_ip = str(helpers.return_key_from_object("127.0.0.1", startparam, "tci_ip"))
            tci_port = str(helpers.return_key_from_object("50001", startparam, "tci_port"))
            enable_mesh = str(helpers.return_key_from_object("False", startparam, "enable_mesh"))
            try:
                # convert ssid list to python list
                ssid_list = str(helpers.return_key_from_object("0, 1, 2, 3, 4, 5, 6, 7, 8, 9", startparam, "ssid_list"))
                ssid_list = ssid_list.replace(" ", "")
                ssid_list = ssid_list.split(",")
                # convert str to int
                ssid_list = list(map(int, ssid_list))
            except KeyError:
                ssid_list = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

            # print some debugging parameters
            for item in startparam:
                log.debug(
                    f"[SCK] Modem Startup Config : {item}",
                    value=startparam[item],
                )

            DAEMON_QUEUE.put(
                [
                    "STARTModem",
                    mycall,
                    mygrid,
                    rx_audio,
                    tx_audio,
                    radiocontrol,
                    rigctld_ip,
                    rigctld_port,
                    enable_scatter,
                    enable_fft,
                    low_bandwidth_mode,
                    tuning_range_fmin,
                    tuning_range_fmax,
                    enable_fsk,
                    tx_audio_level,
                    respond_to_cq,
                    rx_buffer_size,
                    enable_explorer,
                    ssid_list,
                    enable_auto_tune,
                    enable_stats,
                    tx_delay,
                    tci_ip,
                    tci_port,
                    enable_mesh
                ]
            )
            command_response("start_modem", True)

        except Exception as err:
            command_response("start_modem", False)
            log.warning("[SCK] command execution error", e=err, command=received_json)

    def daemon_stop_modem(self, received_json):
        try:
            log.warning("[SCK] Stopping Modem")
            Daemon.modemstarted = False
            # we need to run this twice, otherwise process won't be stopped
            Daemon.modemprocess.kill()
            threading.Event().wait(0.3)
            Daemon.modemprocess.kill()
            # unregister process from atexit to avoid process zombies
            atexit.unregister(Daemon.modemprocess.kill)

            command_response("stop_modem", True)
        except Exception as err:
            command_response("stop_modem", False)
            log.warning("[SCK] command execution error", e=err, command=received_json)

    def daemon_test_hamlib(self, received_json):
        try:
            radiocontrol = str(received_json["parameter"][0]["radiocontrol"])
            rigctld_ip = str(received_json["parameter"][0]["rigctld_ip"])
            rigctld_port = str(received_json["parameter"][0]["rigctld_port"])

            DAEMON_QUEUE.put(
                [
                    "TEST_HAMLIB",
                    radiocontrol,
                    rigctld_ip,
                    rigctld_port,
                ]
            )
            command_response("test_hamlib", True)
        except Exception as err:
            command_response("test_hamlib", False)
            log.warning("[SCK] command execution error", e=err, command=received_json)

    def daemon_start_rigctld(self, received_json):
        """
        hamlib_deviceid: settings.hamlib_deviceid,
        hamlib_deviceport: settings.hamlib_deviceport,
        hamlib_stop_bits: settings.hamlib_stop_bits,
        hamlib_data_bits: settings.hamlib_data_bits,
        hamlib_handshake: settings.hamlib_handshake,
        hamlib_serialspeed: settings.hamlib_serialspeed,
        hamlib_dtrstate: settings.hamlib_dtrstate,
        hamlib_pttprotocol: settings.hamlib_pttprotocol,
        hamlib_ptt_port: settings.hamlib_ptt_port,
        hamlib_dcd: settings.hamlib_dcd,
        hamlbib_serialspeed_ptt: settings.hamlib_serialspeed,
        hamlib_rigctld_port: settings.hamlib_rigctld_port,
        hamlib_rigctld_ip: settings.hamlib_rigctld_ip,
        hamlib_rigctld_path: settings.hamlib_rigctld_path,
        hamlib_rigctld_server_port: settings.hamlib_rigctld_server_port,
        hamlib_rigctld_custom_args: settings.hamlib_rigctld_custom_args
        """
        try:

            hamlib_deviceid = str(received_json["parameter"][0]["hamlib_deviceid"])
            hamlib_deviceport = str(received_json["parameter"][0]["hamlib_deviceport"])
            hamlib_stop_bits = str(received_json["parameter"][0]["hamlib_stop_bits"])
            hamlib_data_bits = str(received_json["parameter"][0]["hamlib_data_bits"])
            hamlib_handshake = str(received_json["parameter"][0]["hamlib_handshake"])
            hamlib_serialspeed = str(received_json["parameter"][0]["hamlib_serialspeed"])
            hamlib_dtrstate = str(received_json["parameter"][0]["hamlib_dtrstate"])
            hamlib_pttprotocol = str(received_json["parameter"][0]["hamlib_pttprotocol"])
            hamlib_ptt_port = str(received_json["parameter"][0]["hamlib_ptt_port"])
            hamlib_dcd = str(received_json["parameter"][0]["hamlib_dcd"])
            hamlbib_serialspeed_ptt = str(received_json["parameter"][0]["hamlib_serialspeed"])
            hamlib_rigctld_port = str(received_json["parameter"][0]["hamlib_rigctld_port"])
            hamlib_rigctld_ip = str(received_json["parameter"][0]["hamlib_rigctld_ip"])
            hamlib_rigctld_path = str(received_json["parameter"][0]["hamlib_rigctld_path"])
            hamlib_rigctld_server_port = str(received_json["parameter"][0]["hamlib_rigctld_server_port"])
            hamlib_rigctld_custom_args = str(received_json["parameter"][0]["hamlib_rigctld_custom_args"])

            DAEMON_QUEUE.put(
                [
                    "START_RIGCTLD",
                    hamlib_deviceid,
                    hamlib_deviceport,
                    hamlib_stop_bits,
                    hamlib_data_bits,
                    hamlib_handshake,
                    hamlib_serialspeed,
                    hamlib_dtrstate,
                    hamlib_pttprotocol,
                    hamlib_ptt_port,
                    hamlib_dcd,
                    hamlbib_serialspeed_ptt,
                    hamlib_rigctld_port,
                    hamlib_rigctld_ip,
                    hamlib_rigctld_path,
                    hamlib_rigctld_server_port,
                    hamlib_rigctld_custom_args
                ]
            )
            command_response("start_rigctld", True)
        except Exception as err:
            command_response("start_rigctld", False)
            log.warning("[SCK] command execution error", e=err, command=received_json)


    def daemon_stop_rigctld(self, received_json):

        try:
            log.warning("[SCK] Stopping rigctld")

            if Daemon.rigctldstarted:
                Daemon.rigctldprocess.kill()
                # unregister process from atexit to avoid process zombies
                atexit.unregister(Daemon.rigctldprocess.kill)

                Daemon.rigctldstarted = False
                command_response("stop_rigctld", True)
        except Exception as err:
            command_response("stop_modem", False)
            log.warning("[SCK] command execution error", e=err, command=received_json)




def send_daemon_state():
    """
    send the daemon state to network
    """
    log = structlog.get_logger("send_daemon_state")

    # we need to do some process checking for providing the correct state
    # at least we are checking the returncode of rigctld
    # None state means, the process is still running
    try:
        retcode_rigctld = Daemon.rigctldprocess
        if retcode_rigctld in [None, "None"]:
            Daemon.rigctldstarted = False
            # This is a blocking code ....
            # output, errs = Daemon.rigctldprocess.communicate()
            # print(f"rigctld out: {output}")
            # print(f"rigctld err: {errs}")
        else:
            # print(f"rigctld closed with code: {retcode_rigctld}")
            Daemon.rigctldstarted = True


        retcode_modem = Daemon.modemprocess
        if retcode_modem in [None, "None"]:
            Daemon.modemstarted = False
            # This is a blocking code ....
            # output, errs = Daemon.modemprocess.communicate()
            # print(f"modem out: {output}")
            # print(f"modem err: {errs}")
        else:
            # print(f"modem closed with code: {retcode_modem}")
            Daemon.modemstarted = True

    except Exception as err:
        log.warning("[DMN] error", e=err)

    try:
        python_version = f"{str(sys.version_info[0])}.{str(sys.version_info[1])}"

        output = {
            "command": "daemon_state",
            "daemon_state": [],
            "rigctld_state": [],
            "python_version": str(python_version),
            "input_devices": AudioParam.audio_input_devices,
            "output_devices": AudioParam.audio_output_devices,
            "serial_devices": Daemon.serial_devices,
            # 'cpu': str(psutil.cpu_percent()),
            # 'ram': str(psutil.virtual_memory().percent),
            "version": Modem.version,
        }

        if Daemon.modemstarted:
            output["daemon_state"].append({"status": "running"})
        else:
            output["daemon_state"].append({"status": "stopped"})

        if Daemon.rigctldstarted:
            output["rigctld_state"].append({"status": "running"})
        else:
            output["rigctld_state"].append({"status": "stopped"})

        return json.dumps(output)
    except Exception as err:
        log.warning("[SCK] error", e=err)
        return None


def send_modem_state():
    """
    send the modem state to network
    """
    encoding = "utf-8"
    output = {
        "command": "modem_state",
        "ptt_state": str(HamlibParam.ptt_state),
        "modem_state": str(Modem.modem_state),
        "arq_state": str(ARQ.arq_state),
        "arq_session": str(ARQ.arq_session),
        "arq_session_state": str(ARQ.arq_session_state),
        "audio_dbfs": str(AudioParam.audio_dbfs),
        "snr": str(ModemParam.snr),
        "frequency": str(HamlibParam.hamlib_frequency),
        "rf_level": str(HamlibParam.hamlib_rf),
        "strength": str(HamlibParam.hamlib_strength),
        "alc": str(HamlibParam.alc),
        "audio_level": str(AudioParam.tx_audio_level),
        "audio_auto_tune": str(AudioParam.audio_auto_tune),
        "speed_level": str(ARQ.arq_speed_level),
        "mode": str(HamlibParam.hamlib_mode),
        "bandwidth": str(HamlibParam.hamlib_bandwidth),
        "fft": str(AudioParam.fft),
        "channel_busy": str(ModemParam.channel_busy),
        "channel_busy_slot": str(ModemParam.channel_busy_slot),
        "is_codec2_traffic": str(ModemParam.is_codec2_traffic),
        "scatter": ModemParam.scatter,
        "rx_buffer_length": str(RX_BUFFER.qsize()),
        "rx_msg_buffer_length": str(len(ARQ.rx_msg_buffer)),
        "arq_bytes_per_minute": str(ARQ.bytes_per_minute),
        "arq_bytes_per_minute_burst": str(ARQ.bytes_per_minute_burst),
        "arq_seconds_until_finish": str(ARQ.arq_seconds_until_finish),
        "arq_seconds_until_timeout": str(ARQ.arq_seconds_until_timeout),
        "arq_compression_factor": str(ARQ.arq_compression_factor),
        "arq_transmission_percent": str(ARQ.arq_transmission_percent),
        "speed_list": ARQ.speed_list,
        "total_bytes": str(ARQ.total_bytes),
        "beacon_state": str(Beacon.beacon_state),
        "stations": [],
        "routing_table": [],
        "mesh_signalling_table" : [],
        "mycallsign": str(Station.mycallsign, encoding),
        "mygrid": str(Station.mygrid, encoding),
        "dxcallsign": str(Station.dxcallsign, encoding),
        "dxgrid": str(Station.dxgrid, encoding),
        "hamlib_status": HamlibParam.hamlib_status,
        "listen": str(Modem.listen),
        "audio_recording": str(AudioParam.audio_record),

    }

    # add heard stations to heard stations object
    for heard in Modem.heard_stations:
        output["stations"].append(
            {
                "dxcallsign": str(heard[0], encoding),
                "dxgrid": str(heard[1], encoding),
                "timestamp": heard[2],
                "datatype": heard[3],
                "snr": heard[4],
                "offset": heard[5],
                "frequency": heard[6],
            }
        )

    for _, route in enumerate(MeshParam.routing_table):
        if MeshParam.routing_table[_][1].hex() == helpers.get_crc_24(b"direct").hex():
            router = "direct"
        else:
            router = MeshParam.routing_table[_][1].hex()
        output["routing_table"].append(
            {
                "dxcall": MeshParam.routing_table[_][0].hex(),
                "router": router,
                "hops": MeshParam.routing_table[_][2],
                "snr": MeshParam.routing_table[_][3],
                "score": MeshParam.routing_table[_][4],
                "timestamp": MeshParam.routing_table[_][5],
            }
        )

    for _, entry in enumerate(MESH_SIGNALLING_TABLE):

        output["mesh_signalling_table"].append(
            {
                "timestamp": MESH_SIGNALLING_TABLE[_][0],
                "destination": MESH_SIGNALLING_TABLE[_][1],
                "origin": MESH_SIGNALLING_TABLE[_][2],
                "frametype": MESH_SIGNALLING_TABLE[_][3],
                "payload": MESH_SIGNALLING_TABLE[_][4],
                "attempt": MESH_SIGNALLING_TABLE[_][5],
                "status": MESH_SIGNALLING_TABLE[_][6],

            }
        )

    try:
        json_out = json.dumps(output)
        return json_out

    except Exception as e:
        log.warning("[SCK] error while json conversion for modem state", e=e, data=output)



def command_response(command, status):
    s_status = "OK" if status else "Failed"
    jsondata = {"command_response": command, "status": s_status}
    data_out = json.dumps(jsondata)
    SOCKET_QUEUE.put(data_out)


def try_except(string):
    try:
        return string
    except Exception:
        return False
