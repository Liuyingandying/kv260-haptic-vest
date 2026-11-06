"""Real native32 DGD1 transaction engine and explicit Linux D1 backend."""
import hashlib
import struct
import time
from .backend import ControlTransaction, ControlBackendError
from .digital_bringup import DigitalFrame, decode_digital, DESIGN_ID


class DigitalTransaction(ControlTransaction):
    FRAME_CLASS = DigitalFrame
    MODE = "DIGITAL_BRINGUP_MODE"

    def decode_packet(self, data):
        return decode_digital(data)

    def sdk_index(self, frame):
        return frame.sdk_frame_index

    def require_online(self, decoded, diagnostics):
        if not diagnostics["online_mask"] & (1 << decoded["selected"]["block"]):
            raise ControlBackendError("D1_SELECTED_BLOCK_NOT_CURRENTLY_ONLINE", details=diagnostics)

    def frame_trace(self, frame):
        return {"selected_source_trace": dict(frame.selected)}

    def __init__(self,transport,*,clock_ns=time.monotonic_ns,emit=None,timeout_ns=1_000_000_000,max_polls=1024):
        if type(timeout_ns) is not int or not 0<timeout_ns<=2_000_000_000:
            raise ValueError('D1_SOFTWARE_BUDGET_MUST_BE_WITHIN_2S_OF_3S_LOCAL_LEASE')
        super().__init__(transport,clock_ns=clock_ns,emit=emit,timeout_ns=min(timeout_ns,250_000_000),max_polls=max_polls)
        self.timeout_ns=timeout_ns

    def _read(self, offset):
        if 0xe00 <= offset <= 0xefc and offset % 4 == 0:
            self._guard()
            self._event('READ32_BEGIN', offset=offset, space='REAL_HAPTIC_ACTIVE')
            value=self.transport.read32(offset)
            if type(value) is not int or not 0<=value<=0xffffffff:
                raise ControlBackendError('NON_U32_HAPTIC_READ_RESULT')
            self._event('READ32_RETURNED', offset=offset, returned_value=value, space='REAL_HAPTIC_ACTIVE')
            self._guard()
            return value
        return super()._read(offset)

    def _diag(self):
        values={}
        for key,offset in (('frame_count',0),('burst_count',4),('flags',8),('online_mask',0x2c),('i2c_error_count',0x40),('timeout_count',0x50)):
            self._guard()
            self._event('STATUS_READ32_BEGIN',offset=offset)
            value=self.transport.read_status32(offset)
            if type(value) is not int or not 0<=value<=0xffffffff:
                raise ControlBackendError('NON_U32_STATUS_READ_RESULT')
            self._event('STATUS_READ32_RETURNED',offset=offset,returned_value=value)
            self._guard();values[key]=value
        return values

    def submit_host_frame(self, frame):
        if getattr(self.transport,'hardware_access',None) is not False:
            raise ControlBackendError('HOST_DIGITAL_ENTRY_REQUIRES_NON_HARDWARE_TRANSPORT')
        return self._execute_digital(frame)

    def _execute_digital(self, frame):
        try:
            self._guard()
            if not isinstance(frame,self.FRAME_CLASS):
                raise ControlBackendError('D1_PRODUCER_FRAME_REQUIRED')
            d=self.decode_packet(frame.packet)
            self.identity={'session':d['session'],'sequence':d['sequence'],'sdk_frame_index':self.sdk_index(frame)}
            self.deadline=self._now()+self.timeout_ns
            self._event('D1_SUBMIT_BEGIN',digital_bringup_mode=True,FRESH=False,capture_age_ns=None)
            before=self._snapshot();diag_before=self._diag()
            if before['status']&0x11:
                raise ControlBackendError('D1_MAILBOX_NOT_IDLE',details=before)
            self.require_online(d,diag_before)
            self._write(0,1)
            for index,word in enumerate(struct.unpack('<96I',frame.packet)):
                self._write(0x100+4*index,word)
            self._write(0,2)
            after=None
            for _ in range(self.max_polls):
                after=self._snapshot()
                if not after['status']&0x10:break
                # Allow the initial digital FULL_OFF/scheduler drain to finish;
                # no access retry, only a bounded poll of current status.
                time.sleep(.001)
            else:raise ControlBackendError('D1_COMMIT_POLL_BUDGET_EXCEEDED',details=after)
            expected=(before['generation']+1)&0xffffffff
            expected_active=(before['legacy_generation']+1)&0xffff
            if (after['generation']!=expected or after['legacy_generation']!=expected_active
                    or after['reason']!=0 or after['status']&0x88 or after['status']&6!=6):
                raise ControlBackendError('D1_DIGITAL_GRANT_OR_ACTIVEGEN_NOT_CONFIRMED',details=after)
            self._event('D1_ACK_OBSERVED',**after)
            gen_before=self._read(8)
            packet_words=[self._read(0x400+4*i) for i in range(96)]
            gen_after=self._read(8)
            returned=struct.pack('<96I',*packet_words)
            self._event('D1_RAW_PACKET_READBACK_CAPTURED',read_words=len(packet_words),
                        generation_before=gen_before,generation_after=gen_after,
                        readback_packet_hex=returned.hex(),
                        readback_packet_sha256=hashlib.sha256(returned).hexdigest(),
                        comparison_result='NOT_YET_COMPARED')
            if gen_before!=expected or gen_after!=expected or returned!=frame.packet:
                raise ControlBackendError('D1_ACCEPTED_PACKET_OR_GENERATION_MISMATCH')
            self.decode_packet(returned)
            active_before=self._read(0x28)
            active_words=[self._read(0xe00+4*i) for i in range(64)]
            active_after=self._read(0x28)
            active_bytes=struct.pack('<64I',*active_words)
            self._event('D1_RAW_HAPTIC_ACTIVE_CAPTURED',read_words=len(active_words),
                        active_generation_before=active_before,active_generation_after=active_after,
                        active_matrix_hex=active_bytes.hex(),active_matrix_sha256=hashlib.sha256(active_bytes).hexdigest(),
                        byte_source='64_NATIVE32_RETURN_VALUES_FROM_REAL_MATRIX_BUFFER_ACT_PORT',
                        comparison_result='NOT_YET_COMPARED')
            if active_before!=expected_active or active_after!=expected_active or active_bytes!=d['matrix']:
                raise ControlBackendError('D1_REAL_HAPTIC_ACTIVE_CONTENT_OR_GENERATION_MISMATCH')
            final=self._snapshot();diag_after=self._diag()
            if (diag_after['i2c_error_count']!=diag_before['i2c_error_count'] or
                    diag_after['timeout_count']!=diag_before['timeout_count']):
                raise ControlBackendError('D1_NEW_I2C_ERROR_OR_TIMEOUT_STOP_NO_RETRY',details={'before':diag_before,'after':diag_after})
            if (final['generation']!=expected or final['legacy_generation']!=expected_active
                    or final['reason'] or final['status']&0x88 or final['status']&6!=6):
                raise ControlBackendError('D1_REVOKED_DURING_READBACK',details=final)
            result={'schema':'R6_D1_DIGITAL_SUBMIT_READBACK_V1',**self.identity,
                    'mode':self.MODE,'accepted_generation':expected,'actual_haptic_active_generation':expected_active,
                    'packet_read_words':len(packet_words),'packet_readback_hex':returned.hex(),
                    'haptic_active_read_words':len(active_words),'haptic_active_matrix_hex':active_bytes.hex(),
                    'packet_sha256':hashlib.sha256(returned).hexdigest(),'haptic_active_sha256':hashlib.sha256(active_bytes).hexdigest(),
                    **self.frame_trace(frame),'diagnostics_before':diag_before,'diagnostics_after':diag_after,
                    'control_adapter_status':final,'hardware_access':getattr(self.transport,'hardware_access',None),
                    'capture_time_verified':False,'capture_age_ns':None,'FRESH':False,
                    'physical_output_permitted':False,'VPLUS_required':'DISCONNECTED',
                    'scope':'DIGITAL_BUFFER_GRANT_NOT_PHYSICAL_MOTOR_ACCEPTANCE'}
            self._event('D1_DIGITAL_READBACK_VERIFIED',accepted_generation=expected,actual_haptic_active_generation=expected_active)
            return result
        except BaseException as exc:
            if self.failed is None:
                self.failed={'error':str(exc),'code':getattr(exc,'code',type(exc).__name__),'identity':dict(self.identity)}
                self._event('FIRST_D1_ERROR_LATCHED',**self.failed)
            raise
        finally:self.deadline=None


class LinuxDigitalBackend:
    FRAME_CLASS = DigitalFrame
    TRANSACTION_CLASS = DigitalTransaction
    DESIGN_ID = DESIGN_ID

    def decode_packet(self, data):
        return decode_digital(data)

    def __init__(self, profile, *, emit=None, clock_ns=time.monotonic_ns,
                 timeout_ns=1_000_000_000,max_polls=1024):
        if profile.expected_design_id!=self.DESIGN_ID:
            raise ValueError('D1_EXPLICIT_PROFILE_ID_0x52364431_REQUIRED')
        self.profile,self.emit,self.clock_ns=profile,emit,clock_ns
        self.timeout_ns,self.max_polls=timeout_ns,max_polls
        self.transaction=None;self.closed=False

    def make_transaction(self, transport):
        return self.TRANSACTION_CLASS(transport, emit=self.emit, clock_ns=self.clock_ns,
                                      timeout_ns=self.timeout_ns, max_polls=self.max_polls)

    def validate_environment(self):
        from .uio import inspect_environment
        return inspect_environment(self.profile)

    def submit(self, frame):
        if self.closed:raise ControlBackendError('D1_BACKEND_CLOSED')
        if not isinstance(frame,self.FRAME_CLASS) or not frame.actual_live:
            raise ControlBackendError('D1_REAL_BACKEND_REQUIRES_CURRENT_LIVE_PRODUCER_FRAME')
        self.decode_packet(frame.packet)
        if self.transaction is None:
            from .uio import open_confirmed_transport
            transport=open_confirmed_transport(self.profile)
            self.transaction=self.make_transaction(transport)
        return self.transaction._execute_digital(frame)

    def close(self):
        self.closed=True
        if self.transaction is not None:self.transaction.close()
