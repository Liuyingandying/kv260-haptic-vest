"""Host-only acquisition identity versus explicit D1 producer-epoch contracts."""
import hashlib,json,struct,types,unittest,zlib
from runtime_control.digital_bringup import DigitalBringupProducer,decode_digital
from host.test_control_paths import IDENTITY,SHA,NOW,compact_fixture

def blob(sequence=1,session=1):
    data=bytearray(compact_fixture())
    struct.pack_into('<I',data,20,session)
    struct.pack_into('<QQ',data,24,sequence,sequence+4)
    struct.pack_into('<I',data,12,0)
    struct.pack_into('<I',data,12,zlib.crc32(data)&0xffffffff)
    return bytes(data)

def producer():
    return DigitalBringupProducer(IDENTITY,native_binary_sha256=SHA,
        source_kind='SAVED_REAL_REPLAY',clock_ns=lambda:NOW)

def prepare(p,data=None,sequence=1,**kw):
    return p.prepare_compact(blob(sequence) if data is None else data,online_mask=0xf0f0,
        receipt_monotonic_ns=NOW-1_000_000_000+sequence-1,**kw)

class ControlSessionContract(unittest.TestCase):
    def test_default_wire_and_v1_record_match_frozen_c4_baseline(self):
        # Independently captured from immutable package_20261106T024131320625Z.
        f=prepare(producer());explicit_none=prepare(producer(),control_session=None)
        self.assertEqual(f.packet,explicit_none.packet)
        self.assertEqual(hashlib.sha256(f.packet).hexdigest(),'6dc742bf9e74d06e749439ba7895eacba6ac82763763d2acd28647f1a6c746d9')
        r=f.as_record()
        self.assertEqual(hashlib.sha256(json.dumps(r,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                         'e296433e592c378c21dd87d7113fe57ff6a1296724f7b2b067be5ad30a2e2152')
        self.assertIsNone(f.acquisition_session)
        self.assertNotIn('producer_session',r);self.assertNotIn('source_acquisition_session',r)
    def test_override_changes_only_word3_and_crc_keeps_original_source(self):
        original=prepare(producer());f=prepare(producer(),control_session=100)
        a=struct.unpack('<96I',original.packet);b=struct.unpack('<96I',f.packet)
        self.assertEqual({i for i in range(96) if a[i]!=b[i]},{3,11})
        self.assertEqual((f.session,f.acquisition_session,f.sequence),(100,1,1))
        self.assertEqual((original.original_matrix,original.original_unknown,original.raw_sha256,
                          original.compact_packet_sha256,original.sdk_frame_index),
                         (f.original_matrix,f.original_unknown,f.raw_sha256,f.compact_packet_sha256,f.sdk_frame_index))
        r=f.as_record();self.assertEqual(r['schema'],'R6_D1_SINGLE_CELL_PROJECTION_V2')
        self.assertEqual((r['source_acquisition_session'],r['producer_session']),(1,100))
        self.assertFalse(r['FRESH']);self.assertFalse(r['physical_output_permitted'])
        self.assertEqual(decode_digital(f.packet)['session'],100)
    def test_control_epochs_nondecreasing_and_native_sequence_continues(self):
        p=producer()
        frames=[prepare(p,sequence=i,control_session=e) for i,e in [(1,100),(2,100),(3,101)]]
        self.assertEqual([(f.session,f.sequence,f.acquisition_session) for f in frames],[(100,1,1),(100,2,1),(101,3,1)])
        with self.assertRaisesRegex(ValueError,'DECREASE_OR_WRAP'):prepare(p,sequence=4,control_session=100)
        with self.assertRaisesRegex(ValueError,'DECREASE_OR_WRAP'):prepare(p,sequence=4)
        self.assertEqual(prepare(p,sequence=4,control_session=101).sequence,4)
    def test_epoch_override_cannot_bypass_native_replay_or_change_session(self):
        p=producer();prepare(p,control_session=100)
        with self.assertRaisesRegex(ValueError,'SOURCE_SESSION_OR_SEQUENCE_REPLAY'):
            prepare(p,control_session=101)
        with self.assertRaisesRegex(ValueError,'SOURCE_SESSION_OR_SEQUENCE_REPLAY'):
            prepare(p,data=blob(2,2),sequence=2,control_session=101)
        self.assertEqual(prepare(p,sequence=2,control_session=101).sequence,2)
    def test_invalid_u32_epoch_rejected_without_consuming_source(self):
        for bad in (0,-1,0x100000000,True,1.0,'2'):
            p=producer()
            with self.assertRaisesRegex(ValueError,'POSITIVE_U32'):prepare(p,control_session=bad)
            self.assertEqual(prepare(p,control_session=0xffffffff).session,0xffffffff)
    def test_owned_entry_passes_override_and_equal_override_is_explicit_v2(self):
        owned=types.SimpleNamespace(frame=types.SimpleNamespace(raw=blob()),
            source={'receipt_monotonic_ns':NOW-1_000_000_000})
        f=producer().prepare_owned(owned,online_mask=0xf0f0,control_session=1)
        self.assertEqual(f.as_record()['schema'],'R6_D1_SINGLE_CELL_PROJECTION_V2')
        self.assertEqual((f.acquisition_session,f.session),(1,1))

if __name__=='__main__':unittest.main(verbosity=2)
