"""Targeted software window close/reserve/rearm and input identity boundaries."""
from pathlib import Path
import ast,copy,hashlib,importlib.util,json,types,unittest
from unittest import mock

TESTS=Path(__file__).resolve().parents[1];ROOT=TESTS.parent;HERE=ROOT/'runtime_night'
spec=importlib.util.spec_from_file_location('_renewal_host',HERE/'run_d1_live.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
STATUS={'frame_count':2,'burst_count':2,'flags':0,'init_done':1,'online_mask':0xf0f0,'runtime_error_mask':0,
        'off_pending':0,'fault_latched':0,'i2c_error_count':0,'timeout_count':0,'terminal_flags':0}
BOOT='00000000-0000-4000-8000-000000000001'

class Tests(unittest.TestCase):
    def phase(self,*,status_change=None,times=None,unknown=False,unsafe_queue=False):
        calls=[];status={**STATUS,**(status_change or {})};record={};log=types.SimpleNamespace(failed=False,pending=False)
        log.event=lambda *a,**k:None
        log.gate=lambda:m.require(not log.failed and not log.pending,'HOST_STOP_LATCHED')
        def call(stage,fn,*args):
            log.gate();log.pending=True
            try:value=fn(*args)
            except BaseException:log.failed=True;raise
            log.pending=False;return value
        log.call=call
        class GPIO:
            owned=True
            last_window_acquire={'reset_verified':True,'after':{'elapsed_cycles':10000}}
            def window_snapshot(self,label):
                return {'label':label,'window_active':True,'window_expired':False,'arm_synced':True,'elapsed_cycles':3_000_000_000}
            def permit(self,on):calls.append(('permit',on))
            def disarm(self):calls.append(('disarm',));self.owned=False
            def acquire(self):calls.append(('acquire',));self.owned=True
        class Native:
            def read(self,off):
                calls.append(('native.read',off))
                if unknown:log.failed=True;raise RuntimeError('HOST_UNKNOWN_MMIO')
                return 1<<14
        class Transport:
            def read32(self,off):calls.append(('read32',off));return {4:8,0x10:1081634186,8:7,0x28:3}[off]
        class Ledger:
            path=Path('HOST_ONLY_LEDGER')
            def reserve(self,active,boot,out):calls.append(('reserve',active));return {'reserved_session':1081634187,'pl_active_session':active}
        def current(*args):calls.append(('status',));return dict(status)
        gpio=GPIO();error=None;returned=None
        ns=times or [31_000_000_000,31_100_000_000,31_200_000_000,32_000_000_000,32_100_000_000]
        seconds=iter([0,0,2]) if unsafe_queue else None
        with mock.patch.object(m,'current_status',side_effect=current),mock.patch.object(m.time,'monotonic_ns',side_effect=ns),\
             mock.patch.object(m.time,'monotonic',side_effect=(lambda:next(seconds)) if unsafe_queue else None,return_value=0),\
             mock.patch.object(m.Path,'read_text',return_value=BOOT):
            try:returned=m.renew_digital_window(Transport(),Native(),gpio,log,dict(STATUS),Ledger(),Path('HOST_ONLY_RUN'),0,record)
            except RuntimeError as exc:error=str(exc)
        return calls,record,returned,error,gpio,log
    def test_mode_is_explicit_60_only_and_8_default_unchanged(self):
        m.validate_window_mode(types.SimpleNamespace(duration_s=8,renew_digital_window=False))
        m.validate_window_mode(types.SimpleNamespace(duration_s=60,renew_digital_window=True))
        for seconds,flag in ((8,True),(60,False)):
            with self.assertRaises(RuntimeError):m.validate_window_mode(types.SimpleNamespace(duration_s=seconds,renew_digital_window=flag))
        self.assertFalse(m.parser().get_default('renew_digital_window'))
    def test_conservative_30s_trigger_and_50s_expiry_stop(self):
        self.assertFalse(m.window_renewal_due(0,29_999_999_999));self.assertTrue(m.window_renewal_due(0,30_000_000_000))
        for ns in (-1,50_000_000_000,50_000_000_001):
            with self.assertRaisesRegex(RuntimeError,'EXPIRED'):m.window_renewal_due(0,ns)
        calls,_,_,error,_,_=self.phase(times=[50_000_000_000])
        self.assertIn('EXPIRED',error);self.assertEqual(calls,[])
    def test_close_reserve_rearm_order_and_fresh_generation_reads(self):
        calls,record,returned,error,gpio,_=self.phase()
        self.assertIsNone(error);self.assertTrue(gpio.owned);self.assertEqual(returned[2]['reserved_session'],1081634187)
        self.assertEqual(calls.count(('permit',False)),1);self.assertEqual(calls.count(('permit',True)),1)
        self.assertLess(calls.index(('permit',False)),calls.index(('disarm',)))
        self.assertLess(calls.index(('disarm',)),calls.index(('reserve',1081634186)))
        self.assertLess(calls.index(('reserve',1081634186)),calls.index(('acquire',)))
        self.assertLess(calls.index(('permit',True)),calls.index(('read32',8)))
        self.assertEqual(record['generations_before_next_fresh_frame'],{'accepted_generation':7,'actual_haptic_active_generation':3})
        self.assertEqual(record['status'],'SAFE_WINDOW_REARMED_WITH_CONTROL_GAP')
        self.assertEqual(record['PCA_register_readback'],'NOT_RUN_DURING_RENEWAL')
    def test_new_iic_error_or_unsafe_off_queue_cannot_rearm(self):
        for options in ({'status_change':{'i2c_error_count':1}}, {'status_change':{'off_pending':0x10},'unsafe_queue':True}):
            with self.subTest(options=options):
                calls,_,_,error,_,_=self.phase(**options)
                self.assertIsNotNone(error);self.assertNotIn(('acquire',),calls)
                self.assertFalse(any(c[0]=='reserve' for c in calls));self.assertEqual(calls.count(('permit',False)),1)
    def test_expiry_while_closing_stops_without_new_reservation(self):
        calls,_,_,error,gpio,_=self.phase(times=[49_000_000_000,50_000_000_000])
        self.assertIn('EXPIRED',error);self.assertFalse(gpio.owned)
        self.assertNotIn(('acquire',),calls);self.assertFalse(any(c[0]=='reserve' for c in calls))
    def test_unknown_access_has_no_disarm_reserve_or_rearm_followup(self):
        calls,_,_,error,_,log=self.phase(unknown=True)
        self.assertEqual(error,'HOST_UNKNOWN_MMIO');self.assertTrue(log.failed)
        self.assertNotIn(('disarm',),calls);self.assertNotIn(('acquire',),calls);self.assertFalse(any(c[0]=='reserve' for c in calls))
    def test_reinit_error_rejects_before_permit_true(self):
        calls=[];gpio=types.SimpleNamespace(acquire=lambda:calls.append('acquire'),permit=lambda on:calls.append(('permit',on)))
        with mock.patch.object(m.time,'monotonic',return_value=0),mock.patch.object(m,'current_status',return_value={**STATUS,'timeout_count':1}):
            with self.assertRaisesRegex(RuntimeError,'INITIALIZATION_IIC_ERRORS'):m.acquire_digital_window(gpio,None,None,require_zero_iic=True)
        self.assertEqual(calls,['acquire'])
    def test_pre_rearm_queued_receipt_is_discarded_without_submission(self):
        worker=next(n for n in ast.parse((HERE/'run_d1_live.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_worker_locked')
        guard=next(n for n in ast.walk(worker) if isinstance(n,ast.If) and isinstance(n.test,ast.BoolOp)
                   and any(isinstance(x,ast.Attribute) and x.attr=='receipt_monotonic_ns' for x in ast.walk(n.test)))
        for enabled,receipt,expected in ((True,100,[]),(True,101,['submit']),(False,100,['submit'])):
            visited=[];result={'pre_renewal_receipt_frames_discarded':0}
            tail=ast.parse("visited.append('submit')").body[0]
            loop=ast.For(target=ast.Name(id='_',ctx=ast.Store()),iter=ast.List(elts=[ast.Constant(0)],ctx=ast.Load()),body=[copy.deepcopy(guard),tail],orelse=[])
            code=compile(ast.fix_missing_locations(ast.Module(body=[loop],type_ignores=[])),'<actual-receipt-gate>','exec')
            exec(code,{'args':types.SimpleNamespace(renew_digital_window=enabled),'item':types.SimpleNamespace(receipt_monotonic_ns=receipt,native={'session':1,'sequence':2}),
                       'receipt_after_renewal_ns':100,'matrix_mode':False,'result':result,'log':types.SimpleNamespace(event=lambda *a,**kw:None),'visited':visited})
            self.assertEqual(visited,expected)
    def test_single_camera_and_native_session_unchanged_by_rearm_and_final_read8_unchanged(self):
        tree=ast.parse((HERE/'run_d1_live.py').read_text());worker=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_worker_locked')
        popens=[n for n in ast.walk(worker) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='Popen']
        self.assertEqual(len(popens),1)
        renew=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='renew_digital_window')
        self.assertFalse(any(isinstance(n,ast.Name) and n.id in ('producer','child','session','source') for n in ast.walk(renew)))
        baseline=TESTS/'fixtures/full_off_readback_reference.py'
        original=ast.parse(baseline.read_text())
        old=next(n for n in original.body if isinstance(n,ast.FunctionDef) and n.name=='full_off_readback')
        current=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='full_off_readback')
        # New R6M2 uses the same read8 engine with an explicit identity;
        # D1 default and every other AST node must remain identical.
        self.assertEqual([a.arg for a in current.args.args],['native','log','expected_id'])
        self.assertEqual(ast.dump(current.args.defaults[0]),ast.dump(ast.Name(id='DESIGN_ID',ctx=ast.Load())))
        normalized=copy.deepcopy(current);normalized.args.args=normalized.args.args[:2];normalized.args.defaults=[]
        guard_calls=[n for n in ast.walk(normalized) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='GuardPins']
        self.assertEqual(len(guard_calls),1);self.assertEqual(guard_calls[0].args[1].id,'expected_id')
        guard_calls[0].args[1]=ast.Name(id='DESIGN_ID',ctx=ast.Load())
        self.assertEqual(ast.dump(old),ast.dump(normalized))
        package=json.loads((ROOT/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8-sig'))
        self.assertTrue(package['supports_digital_window_renewal'])
        self.assertEqual(package['digital_window_renewal_interval_s'],30)
        self.assertEqual(package['hardware_signal_window_s'],50)
