"""Narrow no-hardware tests: actual-window evidence and durable first error."""
from pathlib import Path
import ast,hashlib,importlib.util,json,tempfile,types,unittest
from unittest import mock

TESTS=Path(__file__).resolve().parents[1];ROOT=TESTS.parent;HERE=ROOT/'runtime_night'
spec=importlib.util.spec_from_file_location('_window_telemetry',HERE/'run_d1_live.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class FakeLog:
    def __init__(self,out):
        self.pending=False;self.failed=False;self.stop=Path(out)/'STOP_MMIO';self.events=[]
    def gate(self):m.require(not self.failed and not self.stop.exists(),'HOST_STOP_LATCHED')
    def event(self,*args,**kw):self.events.append((args,kw))


class FakeIO:
    def __init__(self,*,before_flags=0,after_flags=0x25,after_cycles=100000,highs=None):
        self.data=0;self.tri=0xffffffff;self.before_flags=before_flags;self.after_flags=after_flags
        self.after_cycles=after_cycles;self.arm_seen=False;self.accesses=[];self.highs=None if highs is None else iter(highs)
    def write(self,off,value):
        self.accesses.append(('write',off,value))
        if off==8:self.data=value;self.arm_seen=self.arm_seen or bool(value&m.ARM)
        elif off==12:self.tri=value
    def read(self,off):
        self.accesses.append(('read',off))
        if off==8:return self.data
        if off==12:return self.tri
        page=self.data&15
        if page==0:return m.MATRIX_DESIGN_ID
        if page==5:return self.after_flags if self.arm_seen else self.before_flags
        cycles=self.after_cycles if self.arm_seen else 3_000_000_000
        if page==6:return cycles&0xffffffff
        if page==7:return next(self.highs) if self.highs is not None else cycles>>32
        if page==14:return 8|64 if self.arm_seen else 0
        if page in (13,15):return 0
        raise AssertionError(page)


class Tests(unittest.TestCase):
    def gpio(self,out,**kw):
        io=FakeIO(**kw);log=FakeLog(out);return m.D1Gpio(io,log,m.MATRIX_DESIGN_ID),io,log
    def test_acquire_has_real_pre_arm_and_fresh_counter_proof(self):
        with tempfile.TemporaryDirectory() as out:
            gpio,io,log=self.gpio(out);gpio.acquire();proof=gpio.last_window_acquire
            self.assertTrue(proof['reset_verified']);self.assertEqual(proof['before']['page5_raw'],0)
            self.assertEqual(proof['after']['elapsed_cycles'],100000)
            self.assertEqual(proof['after']['nominal_clock_hz'],100_000_000)
            self.assertEqual(proof['after']['window_limit_cycles'],5_000_000_000)
            self.assertEqual(io.data&15,0)
            first_arm=next(i for i,row in enumerate(io.accesses) if row[0]=='write' and row[1]==8 and row[2]&m.ARM)
            self.assertTrue(any(row==('write',8,5) for row in io.accesses[:first_arm]))
    def test_active_expired_sync_or_raw_arm_before_acquire_never_assert_arm(self):
        for flags in (1,2,4,32):
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as out:
                gpio,io,_=self.gpio(out,before_flags=flags)
                with self.assertRaisesRegex(RuntimeError,'BEFORE_ACQUIRE'):gpio.acquire()
                self.assertFalse(io.arm_seen)
    def test_guard_run_without_counter_reset_is_rejected(self):
        with tempfile.TemporaryDirectory() as out:
            gpio,_,_=self.gpio(out,after_cycles=3_000_000_000)
            with self.assertRaisesRegex(RuntimeError,'DID_NOT_RESET'):gpio.acquire()
            self.assertFalse(gpio.last_window_acquire['reset_verified'])
    def test_guard_run_without_actual_window_active_is_rejected(self):
        for flags in (0x24,0x27,0x21):
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as out:
                gpio,_,_=self.gpio(out,after_flags=flags)
                with self.assertRaisesRegex(RuntimeError,'DID_NOT_RESET'):gpio.acquire()
    def test_high_low_high_counter_no_torn_retry(self):
        with tempfile.TemporaryDirectory() as out:
            gpio,io,_=self.gpio(out,highs=[0,1])
            with self.assertRaisesRegex(RuntimeError,'TORN_NO_RETRY'):gpio.window_snapshot('TEST')
            self.assertEqual(sum(row==('write',8,7) for row in io.accesses),2)
    def test_pending_or_failed_mmio_never_reads_diagnostics(self):
        for attr in ('pending','failed'):
            with self.subTest(attr=attr),tempfile.TemporaryDirectory() as out:
                gpio,io,log=self.gpio(out);setattr(log,attr,True)
                with self.assertRaises(RuntimeError):gpio.window_snapshot('TEST')
                self.assertEqual(io.accesses,[])
    def test_first_error_preserves_details_identity_and_source_context(self):
        with tempfile.TemporaryDirectory() as out:
            gpio,io,log=self.gpio(out);gpio.owned=True
            transaction=types.SimpleNamespace(identity={'session':12,'sequence':3},failed={'code':'X'})
            exc=RuntimeError('GRANT_FAILED');exc.code='GRANT_FAILED';exc.details={'reason':7,'status':8,'before':{'generation':2},'after':{'generation':3}}
            context={'source_eligibility':{'rejected_no_eligible_count':8,'last_successful_submit_monotonic_ns':123}}
            result=m.save_first_error(out,exc,log,gpio,types.SimpleNamespace(transaction=transaction),context)
            saved=json.loads((Path(out)/'FIRST_ERROR.json').read_text())
            self.assertEqual(result['status'],'SAVED');self.assertEqual(saved['details'],exc.details)
            self.assertEqual(saved['identity'],transaction.identity);self.assertEqual(saved['context'],context)
            self.assertIsInstance(saved['monotonic_ns'],int)
            diag=json.loads((Path(out)/'FIRST_ERROR_DIAGNOSTICS.json').read_text())
            self.assertEqual(diag['status'],'RETURNED_GPIO_SNAPSHOT_ONLY')
    def test_unknown_failure_persists_without_any_followup_access(self):
        for attr in ('pending','failed','stop'):
            with self.subTest(attr=attr),tempfile.TemporaryDirectory() as out:
                gpio,io,log=self.gpio(out);gpio.owned=True
                if attr=='stop':log.stop.touch()
                else:setattr(log,attr,True)
                m.save_first_error(out,RuntimeError('ORIGINAL'),log,gpio,None,{})
                saved=json.loads((Path(out)/'FIRST_ERROR.json').read_text())
                self.assertEqual(saved['error'],'ORIGINAL');self.assertEqual(io.accesses,[])
                self.assertFalse((Path(out)/'FIRST_ERROR_DIAGNOSTICS.json').exists())
    def test_primary_is_durable_before_any_diagnostic_read(self):
        with tempfile.TemporaryDirectory() as out:
            gpio,_,log=self.gpio(out);gpio.owned=True
            def fail(label):
                self.assertTrue((Path(out)/'FIRST_ERROR.json').is_file())
                raise RuntimeError('READ_FAILED')
            gpio.window_snapshot=fail
            m.save_first_error(out,ValueError('ORIGINAL'),log,gpio,None,{})
            self.assertEqual(json.loads((Path(out)/'FIRST_ERROR.json').read_text())['error'],'ORIGINAL')
            self.assertIn('READ_FAILED',json.loads((Path(out)/'FIRST_ERROR_DIAGNOSTICS.json').read_text())['error'])
    def test_existing_first_error_is_never_overwritten_or_resampled(self):
        with tempfile.TemporaryDirectory() as out:
            gpio,io,log=self.gpio(out);gpio.owned=True
            target=Path(out)/'FIRST_ERROR.json';target.write_text('{"original":true}')
            result=m.save_first_error(out,RuntimeError('SECOND'),log,gpio,None,{})
            self.assertEqual(target.read_text(),'{"original":true}');self.assertEqual(io.accesses,[])
            self.assertEqual(result['status'],'ORIGINAL_FIRST_ERROR_PRESERVED')
    def renewal(self,out,*,after_cycles=10000,unknown=False):
        calls=[];record={};log=FakeLog(out)
        log.call=lambda name,fn,*args:fn(*args)
        class GPIO:
            owned=True
            last_window_acquire={'reset_verified':True,'after':{'elapsed_cycles':after_cycles}}
            def window_snapshot(self,label):
                if unknown:log.failed=True;raise RuntimeError('UNKNOWN_MMIO')
                return {'window_active':True,'window_expired':False,'arm_synced':True,'elapsed_cycles':3_000_000_000}
            def permit(self,value):calls.append(('permit',value))
            def disarm(self):calls.append(('disarm',));self.owned=False
            def acquire(self):calls.append(('acquire',));self.owned=True
        transport=types.SimpleNamespace(read32=lambda off:{4:8,0x10:12,8:3,0x28:4}[off])
        native=types.SimpleNamespace(read=lambda off:1<<14)
        ledger=types.SimpleNamespace(path=Path('HOST_ONLY_LEDGER'),reserve=lambda *args:{'reserved_session':13})
        status={'online_mask':0xf0f0,'init_done':1,'fault_latched':0,'runtime_error_mask':0,
            'i2c_error_count':0,'timeout_count':0,'off_pending':0,'flags':0}
        with mock.patch.object(m.time,'monotonic_ns',return_value=31_000_000_000),\
             mock.patch.object(m.time,'monotonic',return_value=0),\
             mock.patch.object(m,'current_status',return_value=status),\
             mock.patch.object(m.Path,'read_text',return_value='00000000-0000-4000-8000-000000000001'):
            try:
                returned=m.renew_digital_window(transport,native,GPIO(),log,status,ledger,Path(out),0,record)
                return record,calls,returned,None
            except RuntimeError as exc:return record,calls,None,exc
    def test_renewal_keeps_actual_counters_and_refuses_unchanged_counter(self):
        with tempfile.TemporaryDirectory() as out:
            record,_,returned,error=self.renewal(out)
            self.assertIsNone(error);self.assertIsNotNone(returned)
            self.assertEqual(record['actual_window_before_revoke']['elapsed_cycles'],3_000_000_000)
            self.assertEqual(record['actual_window_reacquire']['after']['elapsed_cycles'],10000)
            record,_,returned,error=self.renewal(out,after_cycles=3_000_000_000)
            self.assertIsNone(returned);self.assertIn('COUNTER_NOT_RESET',str(error))
            self.assertNotEqual(record['status'],'SAFE_WINDOW_REARMED_WITH_CONTROL_GAP')
    def test_unknown_pre_renewal_snapshot_cannot_revoke_reserve_or_rearm(self):
        with tempfile.TemporaryDirectory() as out:
            _,calls,returned,error=self.renewal(out,unknown=True)
            self.assertIsNone(returned);self.assertEqual(str(error),'UNKNOWN_MMIO');self.assertEqual(calls,[])
    def test_worker_error_saves_before_log_cleanup_and_eligibility_has_real_updates(self):
        text=(HERE/'run_d1_live.py').read_text();tree=ast.parse(text)
        worker=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_worker_locked')
        calls=[n for n in ast.walk(worker) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='save_first_error']
        self.assertEqual(len(calls),1)
        handler=next(n for n in ast.walk(worker) if isinstance(n,ast.ExceptHandler) and any(c is calls[0] for c in ast.walk(n)))
        self.assertLess(calls[0].lineno,max(n.lineno for n in ast.walk(handler) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='event'))
        self.assertIn("eligibility['rejected_no_eligible_count']+=1",text)
        self.assertIn("result['source_eligibility']['last_successful_submit_monotonic_ns']=time.monotonic_ns()",text)
