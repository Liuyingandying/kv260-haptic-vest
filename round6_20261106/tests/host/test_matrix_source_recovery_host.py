"""Synthetic453 recovery-shape replay; no captured frames, SDK or hardware."""
from pathlib import Path
import ast,copy,hashlib,importlib.util,json,sys,tempfile,types,unittest
from unittest import mock

TESTS=Path(__file__).resolve().parents[1];ROOT=TESTS.parent;HERE=ROOT/'runtime_night'
sys.path[:0]=[str(ROOT),str(ROOT/'runtime_compact')]
spec=importlib.util.spec_from_file_location('_source_recovery',HERE/'run_d1_live.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
from compact_codec import decode
from runtime_control.digital_bringup import _compact
from runtime_control.digital_matrix import DigitalMatrixProducer,matrix_stage_indices

from fixtures.synthetic_sequence import recovery_sequence
from host.test_control_paths import IDENTITY, SHA
STATUS={'online_mask':0xf0f0,'init_done':1,'fault_latched':0,'runtime_error_mask':0,
        'i2c_error_count':0,'timeout_count':0,'off_pending':0,'flags':0}
BOOT='00000000-0000-4000-8000-000000000001'


class Log:
    def __init__(self,out):self.pending=False;self.failed=False;self.stop=Path(out)/'STOP_MMIO';self.events=[]
    def gate(self):m.require(not self.pending and not self.failed and not self.stop.exists(),'NO_FURTHER_ACCESS')
    def event(self,*a,**kw):self.events.append((a,kw))
    def call(self,stage,fn,*args):self.gate();return fn(*args)


class GPIO:
    owned=True;armed=True;last_window_acquire=None
    def window_snapshot(self,label):return {'label':label,'elapsed_cycles':2_000_000,'window_active':True}


class Tests(unittest.TestCase):
    def test_watchdog_bound_precedes_3s_lease_and_unarmed_has_no_false_expiry(self):
        self.assertEqual(m.MATRIX_SOURCE_STALL_NS,750_000_000)
        self.assertFalse(m.matrix_source_watchdog_due(None,99_000_000_000))
        self.assertFalse(m.matrix_source_watchdog_due(1_000_000_000,1_749_999_999))
        self.assertTrue(m.matrix_source_watchdog_due(1_000_000_000,1_750_000_000))
        with self.assertRaisesRegex(RuntimeError,'CLOCK_REVERSED'):m.matrix_source_watchdog_due(100,99)
    def test_sticky_protocol_fault_never_reserves_or_rearms(self):
        with tempfile.TemporaryDirectory() as out:
            log=Log(out);gpio=GPIO();gpio.owned=gpio.armed=False
            transport=types.SimpleNamespace(read32=lambda off:0xe8)
            ledger=types.SimpleNamespace(reserve=mock.Mock(),path=Path('HOST_ONLY_LEDGER'))
            with mock.patch.object(m,'acquire_digital_window') as acquire:
                with self.assertRaisesRegex(RuntimeError,'PROTOCOL_FAULT'):m.resume_matrix_source(transport,gpio,log,ledger,out,{})
                ledger.reserve.assert_not_called();acquire.assert_not_called()
    def test_unknown_mmio_never_holds_or_resumes_again(self):
        for pending,failed in ((True,False),(False,True)):
            with self.subTest(pending=pending,failed=failed),tempfile.TemporaryDirectory() as out:
                log=Log(out);log.pending=pending;log.failed=failed;gpio=GPIO()
                with mock.patch.object(m,'ordered_revoke_disarm') as revoke:
                    with self.assertRaises(RuntimeError):m.hold_matrix_source(None,None,gpio,log,dict(STATUS),{})
                    revoke.assert_not_called()
                gpio.owned=gpio.armed=False
                with self.assertRaises(RuntimeError):m.resume_matrix_source(None,gpio,log,None,out,{})
    def test_iic_failure_during_hold_propagates_no_fake_off_or_resume(self):
        with tempfile.TemporaryDirectory() as out:
            log=Log(out);record={}
            with mock.patch.object(m,'ordered_revoke_disarm',side_effect=RuntimeError('NEW_I2C_ERROR_OR_TIMEOUT_NO_RETRY')):
                with self.assertRaisesRegex(RuntimeError,'NEW_I2C'):m.hold_matrix_source(None,None,GPIO(),log,dict(STATUS),record)
            self.assertEqual(record['status'],'REVOKING_ON_SOURCE_LOSS')
    def test_replay_synthetic453_sequence_keeps_one_sdk_and_renews_control_epoch(self):
        raw=recovery_sequence()
        self.assertEqual(len(raw),453*512)
        packets=[raw[i:i+512] for i in range(0,len(raw),512)]
        frames=[decode(packet) for packet in packets]
        self.assertEqual([f.sequence for f in frames],list(range(1,454)))
        binding=dict(IDENTITY)
        producer=DigitalMatrixProducer(binding,native_binary_sha256=SHA,source_kind='LIVE_NATIVE_PIPE',allowed_target_mask=0xf0f0)
        worker=next(n for n in ast.parse((HERE/'run_d1_live.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_worker_locked')
        matrix_block=next(n for n in ast.walk(worker) if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='matrix_mode'
            and n.body and isinstance(n.body[0],ast.Assign) and isinstance(n.body[0].targets[0],ast.Name) and n.body[0].targets[0].id=='observed')
        receipt_gate=next(n for n in ast.walk(worker) if isinstance(n,ast.If) and isinstance(n.test,ast.BoolOp)
            and any(isinstance(x,ast.Attribute) and x.attr=='receipt_monotonic_ns' for x in ast.walk(n.test)))
        visit=ast.parse('submit_actual(item)').body[0]
        body=[copy.deepcopy(receipt_gate),copy.deepcopy(matrix_block),visit]
        loop=ast.For(target=ast.Name(id='item',ctx=ast.Store()),iter=ast.Name(id='items',ctx=ast.Load()),body=body,orelse=[])
        code=compile(ast.fix_missing_locations(ast.Module(body=[loop],type_ignores=[])),'<actual-worker-matrix-source-branch>','exec')
        items=[types.SimpleNamespace(native={'compact_packet':p,'session':f.session,'sequence':f.sequence,'sdk_frame_index':f.sdk_index},receipt_monotonic_ns=f.encoding_begin_ns+1000) for p,f in zip(packets,frames)]
        submitted=[];holds=[];resumes=[];clock=[frames[0].encoding_begin_ns+1000]
        class Transport:
            active=13
            def read32(self,off):return 8 if off==4 else self.active
        class Ledger:
            path=Path('HOST_ONLY_LEDGER');reserved=13
            def reserve(self,active,boot,out):
                self.reserved=max(self.reserved,active)+1
                return {'reserved_session':self.reserved,'pl_active_session':active}
        with tempfile.TemporaryDirectory() as out:
            log=Log(out);gpio=GPIO();transport=Transport();ledger=Ledger()
            def hold(*args):
                m.hold_matrix_source(*args);holds.append(dict(args[-1]))
            def revoke(tr,native,gp,journal,baseline):
                gp.owned=gp.armed=False;return dict(STATUS)
            def acquire(gp,tr,journal,require_zero_iic=False):
                self.assertTrue(require_zero_iic);gp.owned=gp.armed=True
                gp.last_window_acquire={'reset_verified':True,'after':{'elapsed_cycles':10000}}
                return clock[0],dict(STATUS)
            env={'matrix_mode':True,'matrix_source_held':False,'matrix_last_submit_ns':None,'matrix_hold_record':None,
                'args':types.SimpleNamespace(renew_digital_window=True),'result':{'source_recoveries':[],'session_reservations':[],'pre_renewal_receipt_frames_discarded':0,
                    'source_eligibility':{'rejected_no_eligible_count':0,'last_rejected':None}},'items':items,
                'producer':producer,'_compact':_compact,'log':log,'transport':transport,'native':None,'gpio':gpio,'baseline':dict(STATUS),
                'hold_matrix_source':hold,'resume_matrix_source':m.resume_matrix_source,'session_ledger':ledger,'out':Path(out),
                'control_session':13,'receipt_after_renewal_ns':0,'previous_projection':None,'time':m.time}
            def submit_actual(item):
                f=decode(item.native['compact_packet']);clock[0]=item.receipt_monotonic_ns
                self.assertTrue(gpio.owned and gpio.armed)
                frame=producer.prepare_compact(item.native['compact_packet'],online_mask=0xf0f0,
                    receipt_monotonic_ns=item.receipt_monotonic_ns,now_monotonic_ns=item.receipt_monotonic_ns+10,
                    output_mask=0xf0f0,level_cap=1,allowed_source_indices=matrix_stage_indices(128),control_session=env['control_session'])
                self.assertEqual(frame.source['matrix'],f.matrix)
                self.assertEqual(frame.source['unknown'],f.unknown)
                submitted.append({'sequence':f.sequence,'control_session':frame.session,'acquisition_session':f.session,'unknown':f.unknown_cells})
                transport.active=frame.session
            env['submit_actual']=submit_actual
            # Advance host test clock for every source item, including skipped UNKNOWN records.
            class Items:
                def __iter__(self):
                    for item in items:clock[0]=item.receipt_monotonic_ns;yield item
            env['items']=Items()
            with mock.patch.object(m,'ordered_revoke_disarm',side_effect=revoke),mock.patch.object(m,'acquire_digital_window',side_effect=acquire),\
                 mock.patch.object(m.time,'monotonic_ns',side_effect=lambda:clock[0]),mock.patch.object(m.Path,'read_text',return_value=BOOT):
                exec(code,env)
            known={row['sequence']:row for row in submitted}
            self.assertTrue(all(seq not in known for seq in range(262,355)))
            self.assertNotIn(355,known) # Synthetic recovery trigger intentionally discarded, not replayed.
            first_after=next(row for row in submitted if row['sequence']>355)
            before=known[261]
            self.assertGreater(first_after['control_session'],before['control_session'])
            self.assertEqual(first_after['acquisition_session'],before['acquisition_session'])
            self.assertTrue(all(not row['unknown']==256 for row in submitted))
            self.assertEqual(len({row['acquisition_session'] for row in submitted}),1)
            self.replay_report={'status':'SYNTHETIC_HOST_REPLAY_NOT_HARDWARE','raw_bytes':len(raw),'source_sha256':hashlib.sha256(raw).hexdigest(),
                'CRC_validated_packets':len(frames),'all_unknown_gap_sequences':[262,354],'gap_no_submission':True,
                'recovery_trigger355_discarded':True,'before261':before,'first_after355':first_after,
                'hold_events':len(holds),'control_epoch_reservations':len(env['result']['session_reservations']),
                'distinct_acquisition_sessions':1,'submitted_packets':len(submitted),'hardware_action':False,'SDK_started':False}
    def test_legacy_single_path_remains_outside_matrix_source_hooks(self):
        tree=ast.parse((HERE/'run_d1_live.py').read_text());worker=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_worker_locked')
        parents={child:parent for parent in ast.walk(worker) for child in ast.iter_child_nodes(parent)}
        calls=[n for n in ast.walk(worker) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in ('hold_matrix_source','resume_matrix_source')]
        self.assertEqual(len(calls),3)
        for call in calls:
            ancestors=[];n=call
            while n in parents:n=parents[n];ancestors.append(n)
            self.assertTrue(any(isinstance(n,ast.If) and any(isinstance(x,ast.Name) and x.id=='matrix_mode' for x in ast.walk(n.test)) for n in ancestors))
        self.assertEqual(sum(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='Popen' for n in ast.walk(worker)),1)
