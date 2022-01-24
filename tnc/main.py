#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
Created on Tue Dec 22 16:58:45 2020

@author: DJ2LS

"""


import argparse
import threading
import static
import socketserver
import helpers
import data_handler
import structlog
import log_handler
import modem
import sys


if __name__ == '__main__':

    # --------------------------------------------GET PARAMETER INPUTS
    PARSER = argparse.ArgumentParser(description='FreeDATA TNC')
    PARSER.add_argument('--mycall', dest="mycall", default="AA0AA", help="My callsign", type=str)
    PARSER.add_argument('--mygrid', dest="mygrid", default="JN12AA", help="My gridsquare", type=str) 
    PARSER.add_argument('--rx', dest="audio_input_device", default=0, help="listening sound card", type=int)
    PARSER.add_argument('--tx', dest="audio_output_device", default=0, help="transmitting sound card", type=int)
    PARSER.add_argument('--port', dest="socket_port", default=3000, help="Socket port", type=int)
    PARSER.add_argument('--deviceport', dest="hamlib_device_port", default="/dev/ttyUSB0", help="Hamlib device port", type=str)
    PARSER.add_argument('--devicename', dest="hamlib_device_name", default=2028, help="Hamlib device name", type=str)    
    PARSER.add_argument('--serialspeed', dest="hamlib_serialspeed", default=9600, help="Serialspeed", type=str)    
    PARSER.add_argument('--pttprotocol', dest="hamlib_ptt_type", default='RTS', help="PTT Type", type=str)    
    PARSER.add_argument('--pttport', dest="hamlib_ptt_port", default="/dev/ttyUSB0", help="PTT Port", type=str)        
    PARSER.add_argument('--data_bits', dest="hamlib_data_bits", default="8", help="Hamlib data bits", type=str)         
    PARSER.add_argument('--stop_bits', dest="hamlib_stop_bits", default="1", help="Hamlib stop bits", type=str)          
    PARSER.add_argument('--handshake', dest="hamlib_handshake", default="None", help="Hamlib handshake", type=str)          
    PARSER.add_argument('--radiocontrol', dest="hamlib_radiocontrol", default="direct", help="Set how you want to control your radio")                 
    PARSER.add_argument('--rigctld_port', dest="rigctld_port", default="direct", help="Set rigctld port")    
    PARSER.add_argument('--rigctld_ip', dest="rigctld_ip", default="direct", help="Set rigctld ip")    
 

 
    
    ARGS = PARSER.parse_args()

    static.MYCALLSIGN = bytes(ARGS.mycall, 'utf-8')
    static.MYCALLSIGN_CRC = helpers.get_crc_16(static.MYCALLSIGN)  
    static.MYGRID = bytes(ARGS.mygrid, 'utf-8')
        
    static.AUDIO_INPUT_DEVICE = ARGS.audio_input_device
    static.AUDIO_OUTPUT_DEVICE = ARGS.audio_output_device
    static.PORT = ARGS.socket_port
    static.HAMLIB_DEVICE_NAME = ARGS.hamlib_device_name
    static.HAMLIB_DEVICE_PORT = ARGS.hamlib_device_port
    static.HAMLIB_PTT_TYPE = ARGS.hamlib_ptt_type
    static.HAMLIB_PTT_PORT = ARGS.hamlib_ptt_port
    static.HAMLIB_SERIAL_SPEED = ARGS.hamlib_serialspeed
    static.HAMLIB_DATA_BITS = ARGS.hamlib_data_bits
    static.HAMLIB_STOP_BITS = ARGS.hamlib_stop_bits
    static.HAMLIB_HANDSHAKE = ARGS.hamlib_handshake
    static.HAMLIB_RADIOCONTROL = ARGS.hamlib_radiocontrol
    static.HAMLIB_RGICTLD_IP = ARGS.rigctld_ip
    static.HAMLIB_RGICTLD_PORT = ARGS.rigctld_port
        
    # we need to wait until we got all parameters from argparse first before we can load the other modules
    import sock     
    
    # config logging
    log_handler.setup_logging("tnc")
    structlog.get_logger("structlog").info("[TNC] Starting FreeDATA", author="DJ2LS", year="2022", version="0.1")
    
    # start data handler
    data_handler.DATA()
    
    # start modem
    modem = modem.RF()


    # --------------------------------------------START CMD SERVER

    try:
        structlog.get_logger("structlog").info("[TNC] Starting TCP/IP socket", port=static.PORT)
        # https://stackoverflow.com/a/16641793
        socketserver.TCPServer.allow_reuse_address = True
        cmdserver = sock.ThreadedTCPServer((static.HOST, static.PORT), sock.ThreadedTCPRequestHandler)
        server_thread = threading.Thread(target=cmdserver.serve_forever)
        server_thread.daemon = True
        server_thread.start()

    except Exception as e:
        structlog.get_logger("structlog").error("[TNC] Starting TCP/IP socket failed", port=static.PORT, e=e)
       
