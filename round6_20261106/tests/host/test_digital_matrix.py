"""Host DGM2 contracts. Synthetic G6C1 input is not hardware evidence."""
import json
import struct
import unittest
import zlib

from runtime_control.digital_matrix import DigitalMatrixProducer, decode_matrix_packet
from runtime_control.digital_bringup import decode_digital
from runtime_control.producer import ControlRejected
from host.test_control_paths import IDENTITY, SHA, NOW, compact_fixture


def source_blob(matrix=None, unknown=bytes(32), quality=0, sequence=1, session=1):
    b = bytearray(compact_fixture())
    b[224:480] = bytes(i % 5 for i in range(256)) if matrix is None else bytes(matrix)
    b[480:] = unknown; b[198] = quality
    struct.pack_into('<H', b, 196, sum(x.bit_count() for x in unknown))
    struct.pack_into('<I', b, 20, session)
    struct.pack_into('<QQ', b, 24, sequence, sequence + 4)
    struct.pack_into('<I', b, 12, 0)
    struct.pack_into('<I', b, 12, zlib.crc32(b) & 0xffffffff)
    return bytes(b)


def producer(**kw):
    return DigitalMatrixProducer(IDENTITY, native_binary_sha256=SHA,
        source_kind='SAVED_REAL_REPLAY', clock_ns=lambda: NOW, **kw)


def prepare(p=None, blob=None, sequence=1, **kw):
    return (p or producer()).prepare_compact(source_blob(sequence=sequence) if blob is None else blob,
        online_mask=kw.pop('online_mask', 0xffff),
        receipt_monotonic_ns=kw.pop('receipt_monotonic_ns', NOW - 1_000_000_000 + sequence), **kw)


def changed(packet, *, word=None, value=None, offset=None, byte=None):
    data = bytearray(packet)
    if word is not None: struct.pack_into('<I', data, word * 4, value)
    if offset is not None: data[offset] = byte
    struct.pack_into('<I', data, 44, 0)
    struct.pack_into('<I', data, 44, zlib.crc32(data) & 0xffffffff)
    return bytes(data)


class MatrixBenchContracts(unittest.TestCase):
    def test_full_256_known_cells_and_16_blocks(self):
        f = prepare(level_cap=4); d = decode_matrix_packet(f.packet)
        self.assertEqual(d['matrix'], bytes(i % 5 for i in range(256)))
        self.assertEqual((len(f.packet), d['output_enabled_block_mask'], d['level_cap']), (384, 0xffff, 4))
        self.assertEqual(d['unknown'], bytes(32))
        with self.assertRaises(ValueError): decode_digital(f.packet)

    def test_default_cap_keeps_source_truth_and_does_not_claim_physical_permission(self):
        f = prepare(); r = f.as_record(); d = decode_matrix_packet(f.packet)
        self.assertEqual(d['matrix'], bytes(int(i % 5 != 0) for i in range(256)))
        self.assertEqual(r['original_matrix'], [i % 5 for i in range(256)])
        self.assertEqual(r['level_cap'], 1)
        self.assertTrue(r['capped_source_indices'])
        self.assertFalse(r['actual_live']); self.assertFalse(r['FRESH'])
        self.assertFalse(r['physical_output_permitted']); self.assertIsNone(r['capture_age_ns'])

    def test_unknown_is_preserved_and_nonzero_source_is_explicitly_disabled(self):
        mask = bytearray(32); mask[0] = 2; mask[31] = 0x40
        f = prepare(blob=source_blob([4] * 256, bytes(mask), 1), level_cap=4)
        d = decode_matrix_packet(f.packet); r = f.as_record()
        self.assertEqual(d['unknown'], bytes(mask))
        self.assertEqual((d['matrix'][1], d['matrix'][254]), (0, 0))
        self.assertEqual(r['disabled_nonzero_source_indices'], [1, 254])
        self.assertEqual(r['source_geometry_quality'], 1)
        self.assertEqual(r['original_matrix'], [4] * 256)

    def test_offline_blocks_are_disabled_without_relabeling_unknown(self):
        from runtime_control.digital_bringup import mapping
        f = prepare(blob=source_blob([2] * 256), online_mask=0xf0f0, level_cap=2)
        d = decode_matrix_packet(f.packet)
        self.assertEqual(sum(bool(v) for v in d['matrix']), 128)
        self.assertEqual(d['unknown'], bytes(32))
        for i, v in enumerate(d['matrix']):
            self.assertEqual(v, 2 if 0xf0f0 & (1 << mapping(i)['block']) else 0)

    def test_requested_offline_or_outside_target_mask_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'OFFLINE_OR_OUTSIDE'):
            prepare(online_mask=0xf0f0, output_mask=0xffff)
        with self.assertRaisesRegex(ValueError, 'OFFLINE_OR_OUTSIDE'):
            prepare(producer(allowed_target_mask=0xf0f0), output_mask=0xffff)
        f = prepare(online_mask=0xffff)
        with self.assertRaisesRegex(ValueError, 'OUTSIDE_COMPILED'):
            decode_matrix_packet(f.packet, target_mask=0xf0f0)

    def test_unusable_or_fully_unobservable_cannot_gain_control(self):
        for matrix, quality in ((bytes(256), 3), (bytes(256), 1)):
            with self.subTest(quality=quality), self.assertRaises(ControlRejected):
                prepare(blob=source_blob(matrix, b'\xff' * 32, quality))

    def test_all_zero_known_frame_is_accepted_for_stop(self):
        d = decode_matrix_packet(prepare(blob=source_blob(bytes(256))).packet)
        self.assertEqual(d['matrix'], bytes(256)); self.assertEqual(d['geometry_quality'], 0)

    def test_decoder_enforces_every_cell_cap_unknown_and_mask(self):
        f = prepare(blob=source_blob([1] * 256))
        for label, pkt in (
            ('cap', changed(f.packet, offset=96 + 255, byte=2)),
            ('unknown', changed(f.packet, offset=352 + 31, byte=0x80)),
            ('mask', changed(f.packet, word=12, value=1)),
            ('reserved', changed(f.packet, word=14, value=1)),
            ('unusable', changed(f.packet, word=9, value=3)),
            ('capture', changed(f.packet, word=6, value=123)),
            ('FRESHflags', changed(f.packet, word=10, value=1))):
            with self.subTest(label=label), self.assertRaises(ValueError): decode_matrix_packet(pkt)
        bad = bytearray(f.packet); bad[99] ^= 1
        with self.assertRaisesRegex(ValueError, 'CRC'): decode_matrix_packet(bytes(bad))

    def test_native_replay_cannot_be_bypassed_with_new_control_epoch(self):
        p = producer(); f = prepare(p, control_session=100)
        with self.assertRaisesRegex(ValueError, 'SOURCE_SESSION_OR_SEQUENCE_REPLAY'):
            prepare(p, control_session=101)
        f2 = prepare(p, sequence=2, control_session=101)
        self.assertEqual((f.session, f2.session, f2.source['session']), (100, 101, 1))
        with self.assertRaisesRegex(ValueError, 'DECREASE_OR_WRAP'):
            prepare(p, sequence=3, control_session=100)
        with self.assertRaisesRegex(ValueError, 'SOURCE_SESSION_OR_SEQUENCE_REPLAY'):
            prepare(p, blob=source_blob(sequence=3, session=2), sequence=3, control_session=102)

    def test_invalid_mask_cap_and_clock_types_fail_closed(self):
        for value in (True, 0, -1, 0x10000, '3'):
            with self.subTest(mask=value), self.assertRaises(ValueError): prepare(output_mask=value)
        for value in (True, 0, 5, 1.5):
            with self.subTest(cap=value), self.assertRaises(ValueError): prepare(level_cap=value)
        with self.assertRaisesRegex(ValueError, 'MONOTONIC'):
            prepare(receipt_monotonic_ns=NOW + 1)
        with self.assertRaisesRegex(ValueError, 'POSITIVE_U32'):
            prepare(control_session=0x100000000)

    def test_packet_and_record_keep_original_g6_immutable(self):
        b = source_blob(); before = bytes(b); f = prepare(blob=b)
        self.assertEqual(f.original_compact, before)
        self.assertEqual(b, before)
        r = f.as_record()
        self.assertEqual(r['original_unknown_hex'], r['projection_unknown_hex'])
        self.assertEqual(r['source_acquisition_session'], 1)
        self.assertEqual(r['source_sidebands']['left'], {'kind': 2, 'flags': 5, 'distance_mm': 1200})
        json.dumps(r)


if __name__ == '__main__': unittest.main(verbosity=2)
