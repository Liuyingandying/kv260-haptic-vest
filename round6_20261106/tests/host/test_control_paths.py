"""Targeted HOST-only D1 projection/ABI/error-stop and formal-gate checks."""
from pathlib import Path
import hashlib
import json
import re
import struct
import unittest
from unittest.mock import patch
import zlib

from runtime_control.producer import BoardHealth, ControlProducer, ControlRejected
from runtime_control.backend import LinuxControlBackend, ControlBackendError
from runtime_control.digital_bringup import DigitalBringupProducer, decode_digital, mapping
from runtime_control.digital_backend import DigitalTransaction, LinuxDigitalBackend
from runtime_control.uio import EnvironmentProfile

IDENTITY={'vid':0x2bc5,'pid':0x0614,'uid':'HOST_MODEL_ONLY','serial':'HOST','firmware':'MODEL'}
SHA='a'*64
NOW=10_000_000_000


def compact_fixture(*,unknown_selected=False):
    # HOST synthetic G6C1 bytes, never used with LinuxDigitalBackend.
    b=bytearray(512)
    struct.pack_into('<4sHHI',b,0,b'G6C1',1,224,512)
    struct.pack_into('<II',b,16,15,1)
    struct.pack_into('<10Q',b,24,1,5,NOW-2_000_000_000,NOW-1_900_000_000,
                     NOW-1_800_000_000,NOW-1_700_000_000,NOW-1_600_000_000,
                     NOW-1_500_000_000,123456,123456)
    b[104:136]=bytes.fromhex('b'*64)
    parts=['2bc5:0614',IDENTITY['uid'],IDENTITY['serial'],IDENTITY['firmware']]
    b[136:168]=hashlib.sha256('\x00'.join(parts).encode()).digest()
    struct.pack_into('<HHI',b,168,640,400,1280)
    struct.pack_into('<d',b,176,.001)
    struct.pack_into('<III',b,184,240000,16000,0)
    b[198]=1
    struct.pack_into('<BBH',b,200,2,5,1200)
    struct.pack_into('<BBH',b,204,2,5,1200)
    b[224+128]=3
    b[224+130]=4
    b[480]=1  # unrelated original UNKNOWN remains set
    if unknown_selected:b[480+128//8]|=1<<(128%8)
    struct.pack_into('<H',b,196,sum(x.bit_count() for x in b[480:]))
    struct.pack_into('<I',b,12,zlib.crc32(b)&0xffffffff)
    return bytes(b)


def producer():
    return DigitalBringupProducer(IDENTITY,native_binary_sha256=SHA,
                                 source_kind='SAVED_REAL_REPLAY',clock_ns=lambda:NOW)


def frame():
    return producer().prepare_compact(compact_fixture(),online_mask=0xf0f0,
                                      receipt_monotonic_ns=NOW-1_000_000_000)


class D1HostDevice:
    """Host contract model, deliberately distinct packet and private buffers."""
    hardware_access=False
    def __init__(self,*,corrupt_buffer=False,new_iic_error=False):
        self.staging=[0]*96;self.packet=bytes(384);self.active=bytes(256)
        self.generation=0;self.activegen=0;self.status=8
        self.read_count=0;self.write_count=0;self.act_reads=0
        self.corrupt_buffer=corrupt_buffer;self.new_iic_error=new_iic_error
    def write32(self,off,value):
        self.write_count+=1
        if off==0 and value==1:self.staging=[0]*96;self.status=9
        elif 0x100<=off<=0x27c:self.staging[(off-0x100)//4]=value
        elif off==0 and value==2:
            wire=struct.pack('<96I',*self.staging)
            d=decode_digital(wire)
            self.packet=wire;self.active=bytes(d['matrix'])
            if self.corrupt_buffer:
                b=bytearray(self.active);b[200]^=1;self.active=bytes(b)
            self.generation+=1;self.activegen+=1;self.status=6
        else:raise AssertionError('unexpected write')
    def read32(self,off):
        self.read_count+=1
        if off==4:return self.status
        if off==8:return self.generation
        if off==12:return 0
        if off==0x28:return self.activegen
        if 0x400<=off<=0x57c:return struct.unpack_from('<I',self.packet,off-0x400)[0]
        if 0xe00<=off<=0xefc:
            self.act_reads+=1
            return struct.unpack_from('<I',self.active,off-0xe00)[0]
        raise AssertionError(hex(off))
    def read_status32(self,off):
        return {0:0,4:0,8:0,0x2c:0xf0f0,0x40:int(self.new_iic_error and self.act_reads>=64),0x50:0}[off]


class FormalControlGates(unittest.TestCase):
    def setUp(self):
        self.obs={'session':1,'sequence':9,'matrix_flat':[1]*256,'unknown_mask_bytes':[1]+[0]*31,
                  'unknown_cells':1,'geometry_state':'PARTIAL'}
        self.src={'session':1,'sequence':9,'sdk_frame_index':33,'source_kind':'LIVE_NATIVE_PIPE',
                  'actual_live':True,'capture_time_verified':False,'capture_monotonic_ns':None,
                  'capture_age_ns':None,'receipt_monotonic_ns':NOW-1_000_000}
        self.health=BoardHealth(0xf0f0,False,True,True,False,NOW-1000)
    def test_unknown_capture_partial_and_half_array_remain_rejected(self):
        events=[];d=ControlProducer(emit=events.append).evaluate(self.obs,self.src,health=self.health,now_monotonic_ns=NOW)
        self.assertEqual(d.matrix,bytes([1]*256));self.assertEqual(d.unknown[0],1)
        self.assertIsNone(d.wire);self.assertFalse(d.control_publishable)
        self.assertTrue({'CAPTURE_CLOCK_PROOF_IMPLEMENTATION_MISSING','GEOMETRY_NOT_COMPLETE',
                         'UNKNOWN_REGIONS_PRESENT','ALL16_PCA_HEALTH_REQUIRED'}<=set(d.reasons))
        self.assertEqual(events[0]['sequence'],9);self.assertFalse(events[0]['KPF1_FRESH_emitted'])
    def test_external_true_capture_claim_does_not_mint_proof(self):
        self.src.update(capture_time_verified=True,capture_monotonic_ns=NOW-100)
        d=ControlProducer().evaluate(self.obs,self.src,health=self.health,now_monotonic_ns=NOW)
        self.assertIn('UNSUPPORTED_CAPTURE_PROOF_CLAIM',d.reasons)
        self.assertIsNone(d.wire)
    def test_rejects_before_open_or_hardware_access(self):
        d=ControlProducer().evaluate(self.obs,self.src,now_monotonic_ns=NOW)
        with patch('runtime_control.uio.open_confirmed_transport',side_effect=AssertionError('OPENED')):
            with self.assertRaises(ControlRejected):LinuxControlBackend(None).submit(d)


class DigitalBringupTests(unittest.TestCase):
    def test_partial_old_frame_projects_one_valid_cell_and_retains_original_unknown(self):
        f=frame();d=decode_digital(f.packet);r=f.as_record()
        self.assertEqual(d['selected']['source_index'],128)
        self.assertEqual((d['selected']['block'],d['selected']['channel']),(4,0))
        self.assertEqual(sum(x!=0 for x in d['matrix']),1)
        self.assertEqual(d['matrix'][128],3)
        self.assertEqual(f.original_matrix[130],4)
        self.assertEqual(d['unknown'],f.original_unknown)
        self.assertEqual(struct.unpack_from('<2I',f.packet,24),(0,0))
        self.assertEqual(struct.unpack_from('<I',f.packet,40)[0],3)
        self.assertGreater(r['SDK_delivery_age_ns'],250_000_000)
        self.assertFalse(r['FRESH']);self.assertFalse(r['physical_output_permitted'])
    def test_unknown_selected_cell_is_never_chosen(self):
        f=producer().prepare_compact(compact_fixture(unknown_selected=True),online_mask=0xf0f0,
                                    receipt_monotonic_ns=NOW-1_000_000_000)
        self.assertEqual(f.selected['source_index'],130)
    def test_offline_target_and_sequence_replay_are_rejected(self):
        p=producer()
        with self.assertRaises(ControlRejected):p.prepare_compact(compact_fixture(),online_mask=0x000f,receipt_monotonic_ns=NOW-1000)
        p.prepare_compact(compact_fixture(),online_mask=0xf0f0,receipt_monotonic_ns=NOW-1000)
        with self.assertRaisesRegex(ValueError,'REPLAY'):
            p.prepare_compact(compact_fixture(),online_mask=0xf0f0,receipt_monotonic_ns=NOW-999)
    def test_crc_tamper_and_mapping_tamper_fail(self):
        f=frame();b=bytearray(f.packet);b[100]^=1
        with self.assertRaisesRegex(ValueError,'CRC'):decode_digital(bytes(b))
        bad=bytearray(compact_fixture());bad[250]^=1
        with self.assertRaisesRegex(ValueError,'CRC'):producer().prepare_compact(bytes(bad),online_mask=0xf0f0,receipt_monotonic_ns=NOW-1000)
    def test_inverse_mapping_is_bijective_and_preserves_mirrored_block(self):
        coords={(mapping(i)['block'],mapping(i)['channel']) for i in range(256)}
        self.assertEqual(len(coords),256)
        self.assertEqual((mapping(15)['block'],mapping(15)['channel']),(8,0))
        self.assertEqual((mapping(8)['block'],mapping(8)['channel']),(8,7))
        self.assertEqual(mapping(128)['pca_address'],0x49)
    def test_host_engine_checks_both_real_buffer_and_packet_models(self):
        events=[];dev=D1HostDevice();t=DigitalTransaction(dev,clock_ns=lambda:NOW,emit=events.append)
        result=t.submit_host_frame(frame())
        self.assertEqual(result['accepted_generation'],1)
        self.assertEqual(result['actual_haptic_active_generation'],1)
        self.assertEqual(result['packet_read_words'],96);self.assertEqual(result['haptic_active_read_words'],64)
        self.assertEqual(dev.active,decode_digital(frame().packet)['matrix'])
        self.assertEqual(sum(e['stage']=='READ32_RETURNED' and 0xe00<=e['offset']<=0xefc for e in events),64)
    def test_private_buffer_corruption_is_detected_and_latches_no_retry(self):
        dev=D1HostDevice(corrupt_buffer=True);t=DigitalTransaction(dev,clock_ns=lambda:NOW)
        with self.assertRaisesRegex(ControlBackendError,'REAL_HAPTIC_ACTIVE_CONTENT'):t.submit_host_frame(frame())
        counts=(dev.read_count,dev.write_count)
        with self.assertRaisesRegex(ControlBackendError,'FIRST_ERROR_LATCHED'):t.submit_host_frame(frame())
        self.assertEqual(counts,(dev.read_count,dev.write_count))
    def test_new_iic_error_stops_without_retry(self):
        dev=D1HostDevice(new_iic_error=True);t=DigitalTransaction(dev,clock_ns=lambda:NOW)
        with self.assertRaisesRegex(ControlBackendError,'NEW_I2C_ERROR'):t.submit_host_frame(frame())
        self.assertIsNotNone(t.failed)
    def test_linux_rejects_replay_without_opening(self):
        profile=EnvironmentProfile('d1','HOST_ONLY/dt','HOST_ONLY/bin','a'*64,'HOST_ONLY/helper','b'*64,0x52364431)
        with patch('runtime_control.uio.open_confirmed_transport',side_effect=AssertionError('OPENED')):
            with self.assertRaisesRegex(ControlBackendError,'CURRENT_LIVE'):LinuxDigitalBackend(profile).submit(frame())



if __name__=='__main__':unittest.main(verbosity=2)
