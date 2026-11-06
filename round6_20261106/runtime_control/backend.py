"""KPF1 register transactions. No device access on import or construction.

The public raw-wire entry accepts non-hardware transports only. Linux submit
requires a producer decision first; this producer version intentionally refuses
all current unverified-capture input before even opening UIO. Per-access logs
record actual read32 return values and never substitute submitted bytes.
"""
from dataclasses import replace
import hashlib
import struct
import time
from . import protocol as c
from .producer import ControlDecision, ControlRejected


class ControlBackendError(RuntimeError):
    def __init__(self, code, *, details=None):
        self.code, self.details = code, details or {}
        super().__init__(code)


class ControlTransaction:
    def __init__(self, transport, *, clock_ns=time.monotonic_ns, emit=None,
                 timeout_ns=100_000_000, max_polls=512):
        if type(timeout_ns) is not int or not 0 < timeout_ns <= 250_000_000:
            raise ValueError('BOUNDED_TIMEOUT_REQUIRED')
        if type(max_polls) is not int or not 1 <= max_polls <= 4096:
            raise ValueError('BOUNDED_POLL_BUDGET_REQUIRED')
        self.transport, self.clock_ns, self.emit = transport, clock_ns, emit
        self.timeout_ns, self.max_polls = timeout_ns, max_polls
        self.failed = None
        self.closed = False
        self.last_clock = -1
        self.deadline = None
        self.identity = {}

    def _event(self, stage, **data):
        if self.emit is not None:
            self.emit({'schema': 'R6_KPF1_TRANSACTION_EVENT_V1', 'stage': stage,
                       'monotonic_ns': self._now(), **self.identity, **data})

    def _now(self):
        now = self.clock_ns()
        if type(now) is not int or now < self.last_clock:
            raise ControlBackendError('HOST_CLOCK_NON_MONOTONIC')
        self.last_clock = now
        return now

    def _guard(self):
        if self.closed:
            raise ControlBackendError('CONTROL_BACKEND_CLOSED')
        if self.failed is not None:
            raise ControlBackendError('FIRST_ERROR_LATCHED_NO_FURTHER_ACCESS', details=self.failed)
        if self.deadline is not None and self._now() >= self.deadline:
            raise ControlBackendError('DEADLINE_EXCEEDED_BETWEEN_ACCESSES')

    def _access(self, operation, offset, value=None):
        self._guard()
        if operation == 'READ32':
            if not c.allowed_read(offset):
                raise ControlBackendError('READ_OFFSET_FORBIDDEN')
        elif not c.allowed_write(offset, value):
            raise ControlBackendError('WRITE_OFFSET_OR_COMMAND_FORBIDDEN')
        self._event(operation+'_BEGIN', offset=offset, value=value,
                    software_timeout_cannot_cancel_AXI=True)
        if operation == 'READ32':
            result = self.transport.read32(offset)
            if type(result) is not int or not 0 <= result <= 0xffffffff:
                raise ControlBackendError('NON_U32_READ_RESULT')
        else:
            self.transport.write32(offset, value)
            result = None
        self._event(operation+'_RETURNED', offset=offset, returned_value=result)
        self._guard()
        return result

    def _read(self, offset):
        return self._access('READ32', offset)

    def _write(self, offset, value):
        self._access('WRITE32', offset, value)

    def _snapshot(self):
        return {'status': self._read(c.STATUS), 'reason': self._read(c.REASON),
                'generation': self._read(c.GENERATION),
                'legacy_generation': self._read(c.LEGACY_GENERATION)}

    def submit_host_wire(self, wire):
        """HOST-only digital ABI test; never accepts a hardware transport."""
        if getattr(self.transport, 'hardware_access', None) is not False:
            raise ControlBackendError('RAW_WIRE_ENTRY_REQUIRES_NON_HARDWARE_TRANSPORT')
        return self._execute(wire)

    def _execute(self, wire):
        """Internal engine; LinuxControlBackend owns the live producer gate."""
        try:
            self._guard()
            offered = c.validate_control_wire(wire)
            self.identity = {'session': offered.session, 'sequence': offered.sequence}
            self.deadline = self._now() + self.timeout_ns
            self._event('KPF1_SUBMIT_BEGIN')
            before = self._snapshot()
            if before['status'] & (c.BUSY | c.RX_OPEN):
                raise ControlBackendError('CONTROL_MAILBOX_NOT_IDLE', details=before)
            # Recompute age immediately before BEGIN. This is only legal when
            # capture_ns already has a reviewed same-host monotonic meaning.
            now = self._now()
            age_ns = now-offered.capture_ns
            if not 0 <= age_ns < 250_000_000:
                raise ControlBackendError('VERIFIED_CAPTURE_EXPIRED_OR_FUTURE_BEFORE_BEGIN')
            offered = replace(offered, age_us=(age_ns+999)//1000)
            wire = c.encode_packet(offered)
            c.validate_control_wire(wire)
            self._write(c.COMMAND, 1)
            for index, word in enumerate(struct.unpack('<96I', wire)):
                self._write(c.STAGING+index*4, word)
            self._write(c.COMMAND, 2)
            after = None
            for poll in range(self.max_polls):
                after = self._snapshot()
                if not after['status'] & c.BUSY:
                    break
            else:
                raise ControlBackendError('CONTROL_COMMIT_POLL_BUDGET_EXCEEDED', details=after)
            expected = (before['generation']+1)&0xffffffff
            expected_legacy = (before['legacy_generation']+1)&0xffff
            if (after['generation'] != expected or after['legacy_generation'] != expected_legacy
                    or after['reason'] != 0 or after['status'] & (c.FORCE_OFF | c.PROTOCOL_FAULT)
                    or after['status'] & (c.ACTIVE_VALID | c.CONTROL_VALID) != (c.ACTIVE_VALID | c.CONTROL_VALID)):
                code = c.REASON_NAMES.get(after['reason'], 'UNKNOWN_'+str(after['reason']))
                raise ControlBackendError('CONTROL_COMMIT_NOT_CONFIRMED:'+code, details=after)
            self._event('KPF1_CONTROL_ACK_OBSERVED', **after)
            gen_before = self._read(c.GENERATION)
            values = [self._read(c.ACTIVE+4*i) for i in range(96)]
            gen_after = self._read(c.GENERATION)
            returned = struct.pack('<96I', *values)
            self._event('KPF1_RAW_READBACK_CAPTURED', generation_before=gen_before,
                        generation_after=gen_after, read_words=len(values),
                        readback_packet_hex=returned.hex(),
                        readback_packet_sha256=hashlib.sha256(returned).hexdigest(),
                        submitted_packet_sha256=hashlib.sha256(wire).hexdigest(),
                        comparison_result='NOT_YET_COMPARED',
                        readback_scope='CONTROL_ADAPTER_ACTIVE_PACKET_NOT_PRIVATE_HAPTIC_BANK')
            if gen_before != expected or gen_after != expected:
                raise ControlBackendError('GENERATION_CHANGED_DURING_CONTROL_READBACK')
            if returned != wire:
                raise ControlBackendError('CONTROL_ACTIVE_PACKET_READBACK_MISMATCH')
            c.validate_control_wire(returned)
            final = self._snapshot()
            if (final['generation'] != expected or final['legacy_generation'] != expected_legacy
                    or not final['status'] & c.CONTROL_VALID or final['status'] & c.FORCE_OFF
                    or final['reason'] != 0):
                raise ControlBackendError('CONTROL_REVOKED_DURING_READBACK', details=final)
            self._event('KPF1_CONTROL_READBACK_VERIFIED', **final)
            return {'session': offered.session, 'sequence': offered.sequence,
                    'generation': expected, 'legacy_generation': expected_legacy,
                    'control_valid': True, 'force_off': False, 'status': final,
                    'read_words': len(values), 'readback_packet_hex': returned.hex(),
                    'submitted_packet_hex': wire.hex(), 'matrix_sha256': hashlib.sha256(offered.matrix).hexdigest(),
                    'readback_scope': 'CONTROL_ADAPTER_ACTIVE_PACKET_NOT_PRIVATE_HAPTIC_BANK',
                    'hardware_access': getattr(self.transport, 'hardware_access', None),
                    'physical_output_permitted': False}
        except BaseException as exc:
            if self.failed is None:
                self.failed = {'error': str(exc), 'code': getattr(exc, 'code', type(exc).__name__),
                               'identity': dict(self.identity)}
                self._event('FIRST_CONTROL_ERROR_LATCHED', **self.failed)
            # No retry, ABORT, reset or other register access after failure.
            raise
        finally:
            self.deadline = None

    def close(self):
        self.closed = True
        if hasattr(self.transport, 'close'):
            self.transport.close()


class LinuxControlBackend:
    """Explicit, hash/profile-bound real transport behind the formal producer.

    Current decisions reject before validate/open/MMIO. Completing a reviewed
    capture-clock implementation is a future versioned change, not a bool flag.
    """
    def __init__(self, profile, *, emit=None, clock_ns=time.monotonic_ns):
        self.profile, self.emit, self.clock_ns = profile, emit, clock_ns
        self.transaction = None

    def validate_environment(self):
        from .uio import inspect_environment
        return inspect_environment(self.profile)

    def submit(self, decision):
        if not isinstance(decision, ControlDecision):
            raise ControlRejected(('FORMAL_CONTROL_PRODUCER_DECISION_REQUIRED',))
        wire = decision.require_wire()  # Current unverified capture stops HERE.
        if self.transaction is None:
            from .uio import open_confirmed_transport
            transport = open_confirmed_transport(self.profile)
            self.transaction = ControlTransaction(transport, emit=self.emit, clock_ns=self.clock_ns)
        return self.transaction._execute(wire)

    def close(self):
        if self.transaction is not None:
            self.transaction.close()
