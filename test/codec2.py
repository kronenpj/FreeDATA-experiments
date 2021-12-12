#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ctypes
from ctypes import *
import sys


print("loading module", file=sys.stderr)


# LOAD FREEDV
libname = "libcodec2.so"
api = ctypes.CDLL(libname)

# ctypes function init        

api.freedv_open.argype = [c_int]
api.freedv_open.restype = c_void_p

api.freedv_get_bits_per_modem_frame.argtype = [c_void_p]
api.freedv_get_bits_per_modem_frame.restype = c_int

api.freedv_nin.argtype = [c_void_p]
api.freedv_nin.restype = c_int

api.freedv_rawdatarx.argtype = [c_void_p, c_char_p, c_char_p]
api.freedv_rawdatarx.restype = c_int

api.freedv_get_n_max_modem_samples.argtype = [c_void_p]
api.freedv_get_n_max_modem_samples.restype = c_int

api.freedv_set_frames_per_burst.argtype = [c_void_p, c_int]
api.freedv_set_frames_per_burst.restype = c_void_p
      
api.freedv_get_rx_status.argtype = [c_void_p]
api.freedv_get_rx_status.restype = c_int  
