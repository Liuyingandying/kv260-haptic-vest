"""Synthetic G6C1 recovery shape. No camera frames or real device identity.
453 generated packets include a deliberately unusable93-frame gap. The shape
exercises the public source watchdog/epoch flow; it is not the saved real replay.
"""
import hashlib,struct,zlib
from host.test_control_paths import compact_fixture

def recovery_sequence():
    packets=[]
    for sequence in range(1,454):
        b=bytearray(compact_fixture()); t=1_000_000_000+sequence*66_666_667
        struct.pack_into('<10Q',b,24,sequence,sequence+100,1_000_000_000,t,t+1_000_000,
                         t+2_000_000,t+3_000_000,t+4_000_000,sequence*66667,sequence*66667)
        b[104:136]=hashlib.sha256(('SYNTHETIC_PACKET_'+str(sequence)).encode()).digest()
        b[224:480]=bytes(256); b[480:]=bytes(32); b[480]=1; b[224+128]=1; b[198]=1
        if 262<=sequence<=354:
            b[224:480]=bytes(256);b[480:]=bytes([255])*32;b[198]=3
            struct.pack_into('<BBH',b,200,0,2,0xffff)
            struct.pack_into('<BBH',b,204,0,2,0xffff)
        struct.pack_into('<H',b,196,sum(x.bit_count() for x in b[480:]))
        struct.pack_into('<I',b,12,0);struct.pack_into('<I',b,12,zlib.crc32(b)&0xffffffff)
        packets.append(bytes(b))
    return b''.join(packets)
