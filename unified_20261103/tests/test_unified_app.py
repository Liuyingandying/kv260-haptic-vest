"""Actual frozen C++ -> bridge -> codec tests; synthetic evidence, no hardware."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import unified_app as app
sys.path.insert(0, str(ROOT / 'scripts'))
from build_host_core import build
import kpf1_codec as codec


def bdp(*, sequence=1, capture=1_000_000_000, processing=1_010_000_000,
        holes=False, invalid=False, near=True, session=7):
    width, height = 64, 32
    pixels = [0 if invalid else 3000] * (width * height)
    if near and not invalid:
        # C118: 2x2 supported near component, all four pixels preserved.
        for y in range(14, 16):
            for x in range(28, 30):
                pixels[y * width + x] = 500
    if holes:
        for y in range(0, 2):
            for x in range(16, 18):
                pixels[y * width + x] = 0
    header = app.BDP_HEADER.pack(b'BDP1', width, height, width * 2, 2, session,
                                 sequence, capture, processing, .001, 1, 0)
    return header + struct.pack('<' + str(len(pixels)) + 'H', *pixels)


class UnifiedAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = Path(os.environ.get('UNIFIED_CORE_EXE', ROOT / 'results' / 'host_core' / 'basic_depth.host.exe'))
        cls.config = ROOT / 'core' / 'core_config.txt'
        if not cls.executable.is_file():
            cls.executable = build(cls.executable)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kv260-unified-offline-')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_frames(self, data):
        source = self.root / 'input.bdp'
        source.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        result = app.run_offline([source], self.root / 'output',
                                 executable=self.executable, config=self.config)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        details = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.root / 'output').glob('frame_*.json'))]
        packets = [codec.decode(p.read_bytes()) for p in sorted((self.root / 'output').glob('frame_*.kpf'))]
        for decision in result['results']:
            self.assertFalse(decision['physical_output_permitted'])
            self.assertFalse(decision['current_control_eligible'])
            self.assertEqual(decision['current_live_freshness'], 'NOT_VERIFIED_OFFLINE')
        return result, details, packets

    def test_complete_real_core_and_offline_inhibit(self):
        result, detail, packets = self.run_frames(bdp())
        self.assertEqual(packets[0].quality, codec.Quality.COMPLETE)
        self.assertGreater(packets[0].matrix[118], 0)
        self.assertFalse(any(packets[0].unknown))
        self.assertTrue(detail[0]['decision']['recorded_control_candidate'])
        self.assertFalse(result['hardware_access_performed'])

    def test_partial_retains_near_level_and_unknown(self):
        _, _, packets = self.run_frames(bdp(holes=True))
        p = packets[0]
        self.assertEqual(p.quality, codec.Quality.PARTIAL)
        self.assertGreater(p.matrix[118], 0)
        self.assertTrue(p.unknown[0] & 1)
        self.assertFalse(p.control_candidate)

    def test_unknown_is_not_safe_zero(self):
        _, _, packets = self.run_frames(bdp(invalid=True))
        self.assertEqual(packets[0].quality, codec.Quality.UNKNOWN)
        self.assertEqual(packets[0].unknown, b'\xff' * 32)
        self.assertFalse(packets[0].control_candidate)

    def test_stale_is_unusable(self):
        _, details, packets = self.run_frames(bdp(processing=1_251_000_000))
        self.assertEqual(packets[0].quality, codec.Quality.UNUSABLE)
        self.assertFalse(packets[0].flags & codec.FRESH)
        self.assertEqual(details[0]['core_result']['frame_state']['reason'], 'STALE_FRAME')

    def test_missing_timestamp_is_unusable(self):
        _, detail, packets = self.run_frames(bdp(capture=0, processing=0))
        self.assertEqual(packets[0].quality, codec.Quality.UNUSABLE)
        self.assertEqual(detail[0]['quality_envelope']['legacy_result']['frame_state']['reason'], 'MISSING_RECORDED_TIMESTAMP')
        # The original core result is still retained separately.
        self.assertGreater(detail[0]['core_result']['matrix'][118], 0)

    def test_duplicate_frame_is_rejected_by_actual_core(self):
        _, details, packets = self.run_frames(bdp() + bdp())
        self.assertEqual(details[1]['core_result']['frame_state']['reason'], 'DUPLICATE_FRAME')
        self.assertEqual(packets[1].quality, codec.Quality.UNUSABLE)

    def test_out_of_order_is_unusable(self):
        _, details, packets = self.run_frames(bdp(sequence=2) + bdp(sequence=1))
        self.assertEqual(details[1]['core_result']['frame_state']['reason'], 'OUT_OF_ORDER_FRAME')
        self.assertEqual(packets[1].quality, codec.Quality.UNUSABLE)

    def test_future_is_unusable(self):
        _, details, packets = self.run_frames(bdp(processing=959_000_000))
        self.assertEqual(details[0]['core_result']['frame_state']['reason'], 'FUTURE_FRAME')
        self.assertEqual(packets[0].quality, codec.Quality.UNUSABLE)

    def test_vh_mem_exactly_reconstruct_384_bytes(self):
        _, _, packets = self.run_frames(bdp())
        folder = self.root / 'output'
        words = [int(line, 16) for line in (folder / 'frame_0001.mem').read_text().splitlines() if not line.startswith('//')]
        self.assertEqual(len(words), 96)
        self.assertEqual(struct.pack('<96I', *words), packets[0].encode())
        self.assertEqual((folder / 'frame_0001.vh').read_text().count('kpf_words['), 96)

    def test_existing_kpf_audit_preserves_bytes_not_live(self):
        packet = codec.Packet(7, 1, 1_000_000_000, 10_000, codec.Quality.COMPLETE, codec.FRESH, 0,
                              codec.Side(codec.RiskKind.NO_NEAR, 3000, 5),
                              codec.Side(codec.RiskKind.NO_NEAR, 3000, 5), 1, 64, 32,
                              bytes([3]) + bytes(255), bytes(32))
        source = self.root / 'saved.kpf'
        source.write_bytes(packet.encode())
        result = app.run_offline([source, source], self.root / 'output')
        self.assertEqual((self.root / 'output' / 'frame_0001.kpf').read_bytes(), source.read_bytes())
        details = json.loads((self.root / 'output' / 'frame_0002.json').read_text())
        self.assertEqual(details['temporal_audit'], 'DUPLICATE_PACKET')
        self.assertFalse(result['physical_output_permitted'])

    def test_output_directory_is_never_overwritten(self):
        source = self.root / 'input.bdp'
        source.write_bytes(bdp())
        (self.root / 'output').mkdir()
        sentinel = self.root / 'output' / 'keep.txt'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'NO_OVERWRITE'):
            app.run_offline([source], self.root / 'output', executable=self.executable, config=self.config)
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_malformed_bdp_is_rejected(self):
        for payload in (b'BDP1', bdp()[:-1], b'NOPE' + bdp()[4:]):
            with self.subTest(length=len(payload)):
                with self.assertRaises(ValueError):
                    app.recorded_frames(payload, 'test.bdp')

    def test_mismatched_configuration_is_rejected(self):
        changed = self.root / 'config.txt'
        changed.write_text(self.config.read_text().replace('complete_ratio=0.90', 'complete_ratio=0.80'))
        with self.assertRaisesRegex(ValueError, 'FROZEN_CONFIGURATION_MISMATCH'):
            app.check_config(changed)

    def test_invalid_kpf_rejected(self):
        source = self.root / 'bad.kpf'
        source.write_bytes(bytes(384))
        with self.assertRaises(ValueError):
            app.run_offline([source], self.root / 'output')
        self.assertFalse((self.root / 'output').exists())

    def test_live_mode_has_no_hardware_backend(self):
        proc = subprocess.run([sys.executable, str(ROOT / 'runtime' / 'unified_app.py'),
                               '--mode', 'live', '--input', 'anything.bdp',
                               '--output-dir', str(self.root / 'live')], capture_output=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn(b'LIVE_BACKEND_NOT_IMPLEMENTED_OR_AUTHORIZED', proc.stderr)
        self.assertFalse((self.root / 'live').exists())


if __name__ == '__main__':
    report_path = None
    if '--json-report' in sys.argv:
        i = sys.argv.index('--json-report')
        report_path = Path(sys.argv[i + 1])
        del sys.argv[i:i + 2]
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UnifiedAppTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open('x', encoding='utf-8') as stream:
            json.dump(dict(scope='Host actual frozen C++ + frozen Python bridge/codec; synthetic saved BDP1',
                           tests=result.testsRun, failures=[str(t) + ': ' + e for t, e in result.failures],
                           errors=[str(t) + ': ' + e for t, e in result.errors],
                           skipped=result.skipped, status='PASS' if result.wasSuccessful() else 'FAIL',
                           hardware_action_taken=False), stream, indent=2)
    raise SystemExit(0 if result.wasSuccessful() else 1)
