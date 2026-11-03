"""Bounded JSON bridge to the independently guarded native KV260 mailbox.

No serial, device or MMIO calls are implemented here. Only the reviewed native
child accesses hardware. A missing response permanently freezes this instance;
close() deliberately neither signals nor terminates a possibly hung MMIO child.
"""
from collections import deque
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import queue
import struct
import subprocess
import threading
import time

import kpf1_codec as codec


class BridgeError(RuntimeError):
    pass


class MailboxBridge:
    def __init__(self, executable, expected_sha256, *, timeout=2.0,
                 proc_factory=subprocess.Popen, logger=None,
                 monotonic_ns=time.monotonic_ns):
        self.executable = str(executable)
        self.timeout = float(timeout)
        if not 0 < self.timeout <= 2.0:
            raise ValueError('native deadline must be positive and at most 2 seconds')
        self.logger = logger
        self.monotonic_ns = monotonic_ns
        self.fatal = None
        self.closed = False
        self.proc = None
        self.session = 0
        self.sequence = 0
        self.last_snapshot = None
        self.commits = []
        self.events = deque(maxlen=2048)
        self.stderr = deque(maxlen=4096)
        self.rejected = self.over_age_skips = self.replay_skips = 0
        self.accepted_fresh_count = 0
        self._messages = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        actual = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        if actual != expected_sha256:
            raise BridgeError('NATIVE_SHA256_MISMATCH')
        self.executable_sha256 = actual
        self.proc = proc_factory([self.executable], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 bufsize=0)
        self.pid = self.proc.pid
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self.reader.start()
        self.stderr_reader.start()
        ready = self._receive()
        self._check_snapshot(ready, 'ready')
        self.last_snapshot = ready
        self._event(dict(kind='ready_checked', pid=self.pid, snapshot=ready))

    def _event(self, event):
        self.events.append(event)
        if self.logger:
            self.logger(event)

    def _fatal(self, reason):
        self.fatal = str(reason)
        self._event(dict(kind='fatal', reason=self.fatal,
                         policy='NO_FURTHER_COMMANDS_NO_TERMINATE_NO_RETRY'))
        raise BridgeError(self.fatal)

    def _read_stdout(self):
        try:
            while True:
                line = self.proc.stdout.readline(16385)
                if not line:
                    self._messages.put(('eof', self.proc.poll()), timeout=.1)
                    return
                if len(line) > 16384 or not line.endswith(b'\n'):
                    self._messages.put(('error', 'NATIVE_JSON_LINE_TOO_LARGE_OR_TRUNCATED'), timeout=.1)
                    return
                try:
                    message = json.loads(line)
                except Exception as exc:
                    self._messages.put(('error', 'NATIVE_INVALID_JSON:'+repr(exc)), timeout=.1)
                    return
                self._messages.put(('json', message), timeout=.1)
        except Exception as exc:
            try:
                self._messages.put(('error', repr(exc)), timeout=.1)
            except queue.Full:
                pass

    def _read_stderr(self):
        try:
            while True:
                line = self.proc.stderr.readline(4097)
                if not line:
                    return
                self.stderr.append(line.decode('utf-8', errors='replace').rstrip())
        except Exception as exc:
            self.stderr.append('STDERR_DRAIN_ERROR:'+repr(exc))

    def _ensure_live(self):
        if self.fatal:
            raise BridgeError('HARDWARE_ACCESS_FROZEN:'+self.fatal)
        if self.closed:
            raise BridgeError('BRIDGE_CLOSED')

    def _receive(self):
        self._ensure_live()
        try:
            kind, message = self._messages.get(timeout=self.timeout)
        except queue.Empty:
            self._fatal('NATIVE_RESPONSE_TIMEOUT_2S_OR_SHORTER')
        if kind != 'json' or not isinstance(message, dict):
            self._fatal('NATIVE_'+kind.upper()+':'+str(message))
        return message

    def _command(self, command):
        self._ensure_live()
        try:
            written = self.proc.stdin.write(command)
            if written != len(command):
                self._fatal('NATIVE_STDIN_SHORT_WRITE')
            self.proc.stdin.flush()
        except BridgeError:
            raise
        except Exception as exc:
            self._fatal('NATIVE_STDIN_ERROR:'+repr(exc))
        return self._receive()

    def _safe_status(self, status):
        if type(status) is not int or (status & 12) != 8:
            self._fatal('OUTPUT_INHIBIT_FAILED')

    def _check_snapshot(self, snapshot, kind):
        if snapshot.get('kind') != kind:
            self._fatal('UNEXPECTED_SNAPSHOT_KIND:'+str(snapshot.get('kind')))
        self._safe_status(snapshot.get('status'))
        if snapshot.get('iic_disabled') is not True or snapshot.get('pad_disarmed') is not True:
            self._fatal('IIC_OR_PAD_GUARD_FAILED')
        for key, maximum in (('generation', 0xffffffff), ('active_session', 0xffffffff),
                             ('active_sequence', 0xffffffffffffffff),
                             ('capture_ns', 0xffffffffffffffff), ('now_ns', 0xffffffffffffffff)):
            value = snapshot.get(key)
            if type(value) is not int or not 0 <= value <= maximum:
                self._fatal('INVALID_SNAPSHOT_FIELD:'+key)

    def _snapshot_locked(self):
        result = self._command(b'SNAP')
        self._check_snapshot(result, 'snapshot')
        self.last_snapshot = result
        return result

    def snapshot(self):
        with self._lock:
            return self._snapshot_locked()

    def _assign_identity(self, packet, snapshot):
        active_session = snapshot['active_session']
        new_session = (not self.session or snapshot['status'] & 64 or
                       active_session != self.session)
        if new_session:
            self.session = max(1, self.monotonic_ns() // 1000000,
                               active_session + 1, self.session + 1)
            if self.session > 0xffffffff:
                self._fatal('SESSION_UINT32_EXHAUSTED_NO_WRAP_ALLOWED')
            self.sequence = 1
        else:
            self.sequence = max(self.sequence, snapshot['active_sequence']) + 1
            if self.sequence > 0xffffffffffffffff:
                self._fatal('SEQUENCE_UINT64_EXHAUSTED_NO_WRAP_ALLOWED')
        flags = packet.flags & ~codec.RESUME
        if new_session or snapshot['status'] & 32:
            flags |= codec.RESUME
        return replace(packet, session=self.session, sequence=self.sequence, flags=flags)

    def submit(self, packet):
        """Offer one *real source* snapshot; capture_ns is never rewritten.

        Returns metadata only (including a SHA of the full verified readback),
        not another 384-byte matrix copy. accepted_fresh needs both FRESH and
        the receiver's active_valid bit; a generation advance alone is not it.
        """
        packet.validate()
        with self._lock:
            before = self._snapshot_locked()
            if before['status'] & 0x11:
                self._fatal('MAILBOX_BUSY_OR_UPLOAD_OPEN')
            if packet.flags & codec.FRESH and packet.capture_ns <= before['capture_ns']:
                self.replay_skips += 1
                result = dict(kind='replay_skip', committed=False, accepted_fresh=False,
                              capture_ns=packet.capture_ns,
                              active_capture_ns=before['capture_ns'],
                              generation=before['generation'])
                self._event(result)
                return result
            sent = self._assign_identity(packet, before)
            reply = self._command(b'SUBM'+sent.encode())
            kind = reply.get('kind')
            common = dict(kind=kind, session=sent.session, sequence=sent.sequence,
                          capture_ns=sent.capture_ns, quality=int(sent.quality),
                          flags=sent.flags, unknown_cells=sum(b.bit_count() for b in sent.unknown),
                          source_capture_timestamp_preserved=sent.capture_ns == packet.capture_ns,
                          committed=False, accepted_fresh=False)
            if kind == 'over_age_skip':
                if reply.get('generation') != before['generation']:
                    self._fatal('GENERATION_CHANGED_DURING_OVER_AGE_SKIP')
                self.over_age_skips += 1
                result = dict(reply, **{k:v for k,v in common.items() if k != 'kind'})
            elif kind == 'rejected':
                self._safe_status(reply.get('status'))
                if reply.get('generation') != before['generation']:
                    self._fatal('REJECTION_ADVANCED_GENERATION')
                self.rejected += 1
                result = dict(reply, **{k:v for k,v in common.items() if k != 'kind'})
            elif kind == 'commit':
                self._safe_status(reply.get('status'))
                if (reply.get('generation_before') != before['generation'] or
                    reply.get('generation') != ((before['generation'] + 1) & 0xffffffff)):
                    self._fatal('GENERATION_NOT_EXACTLY_ONCE')
                if reply.get('atomic_preseal') is not True:
                    self._fatal('ATOMIC_PRESEAL_NOT_VERIFIED')
                if type(reply.get('age_us')) is not int:
                    self._fatal('MISSING_REAL_BEGIN_AGE')
                try:
                    expected = replace(sent, age_us=reply['age_us']).encode()
                    active = bytes.fromhex(reply['active_hex'])
                except Exception as exc:
                    self._fatal('INVALID_READBACK_ENCODING:'+repr(exc))
                if len(active) != 384 or len(expected) != 384:
                    self._fatal('READBACK_LENGTH_NOT_384')
                words = struct.unpack('<96I', active)
                expected_words = struct.unpack('<96I', expected)
                mismatches = [i for i, (a,b) in enumerate(zip(words, expected_words)) if a != b]
                if active != expected or mismatches or reply.get('mismatches') != 0:
                    self._fatal('READBACK_BYTES_OR_96_WORDS_MISMATCH:'+str(mismatches))
                for key in ('begin_ns', 'seal_ns', 'verified_ns', 'age_at_seal_us'):
                    if type(reply.get(key)) is not int:
                        self._fatal('MISSING_MONOTONIC_TIMESTAMP:'+key)
                if not reply['begin_ns'] <= reply['seal_ns'] <= reply['verified_ns']:
                    self._fatal('NATIVE_MONOTONIC_TIMESTAMP_REGRESSION')
                actual_seal_age = (reply['seal_ns'] - sent.capture_ns) // 1000
                if abs(reply['age_at_seal_us'] - actual_seal_age) > 1:
                    self._fatal('NATIVE_SEAL_AGE_INCONSISTENT')
                result = dict(common, committed=True,
                    accepted_fresh=bool(sent.flags & codec.FRESH and reply['status'] & 2),
                    generation_before=reply['generation_before'], generation=reply['generation'],
                    status=reply['status'], reason=reply['reason'], atomic_preseal=True,
                    readback_bytes=384, readback_words=96, mismatches=0,
                    readback_sha256=hashlib.sha256(active).hexdigest(),
                    begin_ns=reply['begin_ns'], seal_ns=reply['seal_ns'], verified_ns=reply['verified_ns'],
                    age_us=reply['age_us'], age_at_seal_us=reply['age_at_seal_us'],
                    force_off=True, control_valid=False)
                self.commits.append(result)
                if len(self.commits) > 20000:
                    self._fatal('BOUNDED_COMMIT_RECORD_LIMIT')
                self.accepted_fresh_count += int(result['accepted_fresh'])
            else:
                self._fatal('UNEXPECTED_SUBMIT_REPLY:'+str(kind))
            self._event(result)
            return result

    def close(self):
        """Normal QUIT only. A fatal instance never sends more commands/signals."""
        if self.fatal:
            return dict(status='FROZEN', pid=self.pid, fatal=self.fatal,
                        command_sent=False, terminated=False)
        if self.closed:
            return dict(status='ALREADY_CLOSED', command_sent=False)
        with self._lock:
            final = self._command(b'QUIT')
            self._check_snapshot(final, 'final')
            try:
                code = self.proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self._fatal('NATIVE_EXIT_TIMEOUT_NO_TERMINATE')
            if code:
                self._fatal('NATIVE_NONZERO_EXIT:'+str(code))
            self.closed = True
            self.last_snapshot = final
            self.reader.join(timeout=.2)
            self.stderr_reader.join(timeout=.2)
            for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                pipe.close()
            result = dict(status='PASS', final=final, exit_code=code,
                          command_sent=True, terminated=False)
            self._event(dict(kind='normal_close', **result))
            return result
