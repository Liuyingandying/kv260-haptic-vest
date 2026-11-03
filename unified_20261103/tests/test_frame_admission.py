"""Pure Host scheduling tests; no live integration or hardware acceptance."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import frame_admission as admission
import kpf1_codec as codec

BASE = 1_000_000_000


def packet(sequence=1, *, session=10, capture=None, quality=codec.Quality.COMPLETE,
           resume=True, fault=0):
    capture = BASE + sequence * 1_000_000 if capture is None else capture
    matrix = bytes([3]) + bytes(255)
    mask = bytes(32)
    flags = codec.FRESH | (codec.RESUME if resume else 0)
    sides = codec.Side(codec.RiskKind.NO_NEAR, 3000, 5)
    if quality == codec.Quality.PARTIAL:
        mask = bytes([2]) + bytes(31)
    elif quality in (codec.Quality.UNKNOWN, codec.Quality.UNUSABLE):
        matrix, mask, sides = bytes(256), b'\xff' * 32, codec.Side()
    if quality == codec.Quality.UNUSABLE:
        flags &= ~codec.FRESH
    return codec.Packet(session, sequence, capture, 0, quality, flags, fault,
                        sides, sides, 1, 640, 480, matrix, mask).validate()


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.s = admission.FrameAdmission()

    def offer(self, p, *, busy=False, now=None, verified=True, **kwargs):
        return self.s.offer(p, now_ns=p.capture_ns + 10_000_000 if now is None else now,
                            clock_verified=verified, pipeline_quiescent=not busy, **kwargs)

    def test_verified_complete_idle_is_admitted_host_only(self):
        p = packet()
        a = self.offer(p)
        self.assertEqual(a.kind, 'SUBMIT_COMPLETE')
        self.assertTrue(a.admission_allowed)
        self.assertFalse(a.physical_output_permitted)
        self.assertEqual(a.packet.capture_ns, p.capture_ns)
        self.assertEqual(a.packet.age_us, 10_000)

    def test_busy_keeps_exactly_latest_and_overwrites(self):
        for i in range(1, 101):
            a = self.offer(packet(i), busy=True)
            self.assertEqual(a.kind, 'QUEUED_LATEST_COMPLETE')
            self.assertEqual(self.s.pending_count, 1)
        self.assertEqual(self.s.snapshot()['overwritten'], 99)
        a = self.s.drain(now_ns=BASE + 111_000_000, clock_verified=True, pipeline_quiescent=True)
        self.assertEqual(a.packet.sequence, 100)
        self.assertEqual(a.packet.capture_ns, BASE + 100_000_000)
        self.assertEqual(self.s.pending_count, 0)

    def test_pending_rechecks_exact_250ms_and_does_not_refresh_capture(self):
        p = packet()
        self.offer(p, busy=True)
        a = self.s.drain(now_ns=p.capture_ns + 250_000_000, clock_verified=True, pipeline_quiescent=True)
        self.assertEqual(a.kind, 'SUBMIT_OBSERVATION_INHIBIT')
        self.assertEqual(a.reason, 'STALE_AT_ADMISSION')
        self.assertEqual(a.packet.quality, codec.Quality.UNUSABLE)
        self.assertEqual(a.packet.capture_ns, p.capture_ns)
        self.assertFalse(a.packet.flags & codec.FRESH)
        self.assertTrue(self.s.new_session_required)

    def test_partial_preempts_busy_and_keeps_near_and_unknown(self):
        self.offer(packet(), busy=True)
        p = packet(2, quality=codec.Quality.PARTIAL)
        a = self.offer(p, busy=True)
        self.assertEqual(a.kind, 'SUBMIT_OBSERVATION_INHIBIT')
        self.assertTrue(a.inhibit_required)
        self.assertEqual(a.packet.matrix, p.matrix)
        self.assertEqual(a.packet.unknown, p.unknown)
        self.assertEqual(self.s.pending_count, 0)
        self.assertEqual(self.s.drain(now_ns=BASE + 30_000_000, clock_verified=True,
                                      pipeline_quiescent=True).kind, 'NO_PENDING')

    def test_unknown_preempts_busy(self):
        self.offer(packet(), busy=True)
        a = self.offer(packet(2, quality=codec.Quality.UNKNOWN), busy=True)
        self.assertEqual(a.kind, 'SUBMIT_OBSERVATION_INHIBIT')
        self.assertEqual(a.packet.unknown, b'\xff' * 32)
        self.assertFalse(self.s.new_session_required)

    def test_unusable_preempts_and_requires_new_session(self):
        self.offer(packet(), busy=True)
        a = self.offer(packet(2, quality=codec.Quality.UNUSABLE, fault=4), busy=True)
        self.assertEqual(a.kind, 'SUBMIT_OBSERVATION_INHIBIT')
        self.assertTrue(self.s.new_session_required)
        a = self.offer(packet(3), now=BASE + 30_000_000)
        self.assertEqual(a.reason, 'RECOVERY_REQUIRES_NEW_SESSION')

    def test_source_fault_preempts_without_mutating_original(self):
        p = packet()
        a = self.offer(p, busy=True, source_fault='CAPTURE_EXITED')
        self.assertTrue(a.inhibit_required)
        self.assertEqual(a.packet.quality, codec.Quality.UNUSABLE)
        self.assertEqual(p.quality, codec.Quality.COMPLETE)
        self.assertEqual(a.packet.session, p.session)
        self.assertEqual(a.packet.sequence, p.sequence)
        self.assertEqual(a.packet.capture_ns, p.capture_ns)

    def test_clock_unverified_default_never_admits_complete(self):
        p = packet()
        a = self.s.offer(p, now_ns=p.capture_ns, pipeline_quiescent=True)
        self.assertEqual(a.reason, 'CLOCK_MAPPING_NOT_VERIFIED')
        self.assertEqual(a.packet.quality, codec.Quality.UNUSABLE)
        self.assertFalse(a.packet.flags & codec.FRESH)
        self.assertEqual(a.packet.age_us, p.age_us)

    def test_queued_frame_clock_evidence_must_be_reconfirmed(self):
        self.offer(packet(), busy=True)
        a = self.s.drain(now_ns=BASE + 20_000_000, pipeline_quiescent=True)
        self.assertEqual(a.kind, 'SUBMIT_OBSERVATION_INHIBIT')
        self.assertEqual(a.reason, 'CLOCK_MAPPING_NOT_VERIFIED')

    def test_explicit_resume_required_initially(self):
        a = self.offer(packet(resume=False))
        self.assertEqual(a.reason, 'EXPLICIT_RESUME_FLAG_REQUIRED')
        a = self.offer(packet(2, resume=True))
        self.assertEqual(a.kind, 'SUBMIT_COMPLETE')

    def test_partial_requires_explicit_resume_for_next_complete(self):
        self.offer(packet())
        self.offer(packet(2, quality=codec.Quality.PARTIAL), busy=True)
        a = self.offer(packet(3, resume=False))
        self.assertEqual(a.reason, 'EXPLICIT_RESUME_FLAG_REQUIRED')
        a = self.offer(packet(4, resume=True))
        self.assertEqual(a.kind, 'SUBMIT_COMPLETE')

    def test_new_session_and_resume_after_unusable(self):
        self.offer(packet(quality=codec.Quality.UNUSABLE, fault=4))
        a = self.offer(packet(2, session=11, resume=False))
        self.assertEqual(a.reason, 'EXPLICIT_RESUME_FLAG_REQUIRED')
        a = self.offer(packet(3, session=11, resume=True))
        self.assertEqual(a.kind, 'SUBMIT_COMPLETE')

    def test_duplicate_and_reordered_never_admitted(self):
        self.offer(packet(3))
        for p in (packet(3), packet(2)):
            a = self.offer(p)
            self.assertEqual(a.kind, 'REJECT')
            self.assertTrue(a.inhibit_required)

    def test_old_session_never_restored(self):
        self.offer(packet(session=11))
        a = self.offer(packet(2, session=10))
        self.assertEqual(a.reason, 'RETIRED_OR_OLD_SESSION')

    def test_new_sequence_with_replayed_capture_is_rejected(self):
        self.offer(packet())
        a = self.offer(packet(2, capture=BASE + 1_000_000))
        self.assertEqual(a.reason, 'CAPTURE_WATERMARK_REPLAY')

    def test_host_clock_regression_fail_closed(self):
        self.offer(packet(), now=BASE + 50_000_000)
        a = self.offer(packet(2), now=BASE + 40_000_000)
        self.assertEqual(a.reason, 'HOST_MONOTONIC_CLOCK_REGRESSION')
        self.assertFalse(a.packet.flags & codec.FRESH)

    def test_invalid_schema_clears_pending(self):
        self.offer(packet(), busy=True)
        a = self.s.offer({'quality': 0}, now_ns=BASE, clock_verified=True, pipeline_quiescent=True)
        self.assertEqual(a.kind, 'REJECT')
        self.assertTrue(a.inhibit_required)
        self.assertEqual(self.s.pending_count, 0)

    def test_busy_flag_must_be_explicit_true_for_quiescence(self):
        a = self.s.offer(packet(), now_ns=BASE + 20_000_000, clock_verified=True)
        self.assertEqual(a.kind, 'QUEUED_LATEST_COMPLETE')
        a = self.s.drain(now_ns=BASE + 30_000_000, clock_verified=True, pipeline_quiescent=1)
        self.assertEqual(a.kind, 'QUEUED_LATEST_COMPLETE')


if __name__ == '__main__':
    report = None
    if '--json-report' in sys.argv:
        i = sys.argv.index('--json-report'); report = Path(sys.argv[i + 1]); del sys.argv[i:i + 2]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AdmissionTest))
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open('x', encoding='utf-8') as stream:
            json.dump(dict(scope='Host scheduling model only; not integrated with live runtime',
                           tests=result.testsRun, failures=[str(t) + ': ' + e for t, e in result.failures],
                           errors=[str(t) + ': ' + e for t, e in result.errors],
                           status='PASS' if result.wasSuccessful() else 'FAIL',
                           hardware_action_taken=False), stream, indent=2)
    raise SystemExit(0 if result.wasSuccessful() else 1)
