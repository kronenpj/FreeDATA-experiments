#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from enum import Enum


class FRAME_TYPE(Enum):
    """Lookup for frame types"""

    BURST_01 = 10
    BURST_02 = 11
    BURST_03 = 12
    BURST_04 = 13
    # ...
    BURST_51 = 50
    BURST_ACK = 60
    FR_ACK = 61
    FR_REPEAT = 62
    FR_NACK = 63
    BURST_NACK = 64
    MESH_BROADCAST = 100
    MESH_SIGNALLING_PING = 101
    MESH_SIGNALLING_PING_ACK = 102
    CQ = 200
    QRV = 201
    PING = 210
    PING_ACK = 211
    IS_WRITING = 215
    ARQ_SESSION_OPEN = 221
    ARQ_SESSION_HB = 222
    ARQ_SESSION_CLOSE = 223
    ARQ_DC_OPEN_W = 225
    ARQ_DC_OPEN_ACK_W = 226
    ARQ_DC_OPEN_N = 227
    ARQ_DC_OPEN_ACK_N = 228
    ARQ_STOP = 249
    BEACON = 250
    FEC = 251
    FEC_WAKEUP = 252
    IDENT = 254
    TEST_FRAME = 255