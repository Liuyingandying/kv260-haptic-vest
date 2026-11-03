"""Actual KV260 D455 -> frozen core -> atomic mailbox. No IIC writes."""
import argparse,base64,hashlib,json,os,sys,time,zlib,traceback,threading,queue
from collections import Counter
from dataclasses import replace
from pathlib import Path
from mailbox_bridge import MailboxBridge
from gc_window import GCTimeline
import kpf1_codec as codec
import live_pl_loop as live
from live_ram_bootstrap import bootstrap

class LiveBridge(MailboxBridge):
    def __init__(self,*a,**k):
        self.last_source_packet=None;self.loss=None;self.monitor_next=0;self.monitor_start=None;self.progress=[];self.monitor_drops=0
        self.monq=queue.Queue(maxsize=1);self.monitor_stop=threading.Event();self.monitor_error=None
        super().__init__(*a,**k)
        def writer():
            try:
                while not self.monitor_stop.is_set() or not self.monq.empty():
                    try:r=self.monq.get(timeout=.2)
                    except queue.Empty:continue
                    print('LIVE_PL_MONITOR '+json.dumps(r,separators=(',',':')),flush=True)
            except Exception as e:self.monitor_error=repr(e)
        self.monitor_thread=threading.Thread(target=writer,daemon=True);self.monitor_thread.start()
    def submit(self,packet):
        r=super().submit(packet)
        if packet.flags&codec.FRESH:
            self.last_source_packet=packet
            if self.loss and self.loss.get('resume') is None and r.get('accepted_fresh'):
                assert r['session']>self.loss['gap_commit']['session']
                assert packet.capture_ns>self.loss['last_source_capture_ns']
                self.loss['resume']=r;print('SOFTWARE_LOSS_RECOVERED '+json.dumps(r),flush=True)
        return r
    def software_delivery_loss(self,seconds):
        assert self.last_source_packet is not None and self.loss is None
        self.loss={'kind':'SOFTWARE_DELIVERY_PAUSE_NOT_PHYSICAL_UNPLUG','begin_ns':time.monotonic_ns(),'duration_requested_s':seconds,'last_source_capture_ns':self.last_source_packet.capture_ns,'before':self.snapshot(),'resume':None}
        print('SOFTWARE_LOSS_BEGIN '+json.dumps(self.loss),flush=True)
        time.sleep(.4)
        expired=self.snapshot();self.loss['expired']=expired
        assert not(expired['status']&2) and expired['status']&64 and expired['generation']==self.loss['before']['generation'],'PL must expire without repeated frame submissions'
        packet=replace(self.last_source_packet,quality=codec.Quality.UNUSABLE,flags=codec.GAP,producer_fault=1,matrix=bytes(256),unknown=b'\xff'*32,left=codec.Side(),right=codec.Side())
        gap=super().submit(packet);self.loss['gap_commit']=gap
        assert gap['committed'] and not gap['accepted_fresh'] and gap['unknown_cells']==256
        time.sleep(max(0,seconds-(time.monotonic_ns()-self.loss['begin_ns'])/1e9))
        after=self.snapshot();self.loss['after']=after;self.loss['end_ns']=time.monotonic_ns()
        assert after['generation']==gap['generation'] and not(after['status']&2) and after['status']&64
        print('SOFTWARE_LOSS_END '+json.dumps(self.loss),flush=True)
    def monitor(self,row,state,slot,n):
        now=time.monotonic_ns()
        if self.monitor_start is None:self.monitor_start=now
        if now<self.monitor_next:return
        self.monitor_next=now+1000000000
        r={'source':'LIVE_D455','snapshot':'LAST_COMPLETED_REAL_OUTPUT','camera_sequence':row['camera_sequence'],'frames':n,'quality':row['output_quality'],'unknown_cells':row['unknown_cells'],'source_capture_ns':row['capture_ns'],'age_at_matrix_ready_ms':row['output_age_ms'],'pl':row['pl'],'matrix_16x16':[''.join('?' if row['unknown_lsb_first_hex'] and (bytes.fromhex(row['unknown_lsb_first_hex'])[i//8]>>(i%8))&1 else str(row['matrix'][i]) for i in range(j*16,(j+1)*16)) for j in range(16)],'slot':slot,'physical_output_permitted':False}
        if self.monq.full():
            try:self.monq.get_nowait();self.monitor_drops+=1
            except queue.Empty:pass
        self.monq.put_nowait(r)
    def report(self):
        fresh=[r for r in self.commits if r['accepted_fresh']]
        return {'native_sha256':self.executable_sha256,'native_pid':self.pid,'fatal':self.fatal,'committed_observations':len(self.commits),'accepted_fresh':len(fresh),'nonfresh_events':len(self.commits)-len(fresh),'rejected':self.rejected,'over_age_skips':self.over_age_skips,'replay_skips':self.replay_skips,'age_at_seal_ms':live.distribution([r['age_at_seal_us']/1000 for r in fresh]),'native_transfer_ms':live.distribution([(r['verified_ns']-r['begin_ns'])/1e6 for r in self.commits]),'pl_valid_fps':(len(fresh)-1)/((fresh[-1]['seal_ns']-fresh[0]['seal_ns'])/1e9) if len(fresh)>1 else None,'quality_counts':dict(Counter(str(r['quality']) for r in self.commits)),'unknown_cells':live.distribution([r['unknown_cells'] for r in self.commits]),'generation_first':self.commits[0]['generation_before'] if self.commits else None,'generation_last':self.commits[-1]['generation'] if self.commits else None,'all_96_word_checks':all(r['readback_words']==96 and r['mismatches']==0 for r in self.commits),'all_atomic_preseal':all(r['atomic_preseal'] for r in self.commits),'readback_mismatches':sum(r['mismatches'] for r in self.commits),'force_off':True,'control_valid':False,'iic_disabled':True,'pad_disarmed':True,'loss':self.loss,'monitor_drops':self.monitor_drops,'timestamp_basis':'Actual V4L2 MONOTONIC capture -> CLOCK_MONOTONIC immediately after mailbox SEAL; Windows transport excluded'}
    def finish(self):
        time.sleep(.4)
        terminal=self.snapshot();assert not(terminal['status']&2) and terminal['status']&64
        close=self.close();self.monitor_stop.set();self.monitor_thread.join(timeout=3)
        assert not self.monitor_thread.is_alive() and not self.monitor_error
        return {'expired_after_stop':terminal,'native_close':close,'native_stderr':list(self.stderr)}

def main():
    a=argparse.ArgumentParser();a.add_argument('--seconds',type=int,choices=[60,150,600],required=True);a.add_argument('--software-loss-at',type=float);a.add_argument('--defer-gc',action='store_true');args=a.parse_args()
    root=Path(__file__).resolve().parent;manifest=json.loads((root/'runtime_manifest.json').read_text())
    for name,h in manifest['sha256'].items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==h,name
    assert os.uname().machine=='aarch64' and os.getuid()==0
    _,evidence=bootstrap((root/'scale_proof.bundle').read_bytes(),validate_only=True)
    live.checked_scale(evidence)
    identity=__import__('v4l2_support').camera_guard('/dev/video0');print('LIVE_CAMERA_IDENTITY '+json.dumps(identity),flush=True)
    b=LiveBridge(root/'live_mailbox.aarch64',manifest['sha256']['live_mailbox.aarch64'])
    live.BRIDGE=b;live.SOFTWARE_LOSS_AT=args.software_loss_at
    saved={}
    def export(rows,view):
        saved['rows']=[{k:r[k] for k in ['camera_sequence','session','capture_ns','matrix_ready_ns','output_age_ms','full_processing_ms','output_quality','unknown_cells','pl']} for r in rows]
        saved['last_matrix']=rows[-1]['matrix'] if rows else None;saved['last_unknown_hex']=rows[-1]['unknown_lsb_first_hex'] if rows else None
    live.export_compressed=export
    # Preserve metadata/algorithm measurements; avoid huge repeated stdout arrays.
    orig_print=print
    import builtins
    def local_print(*items,**kw):
        if items and isinstance(items[0],str) and items[0].startswith('KV260_LIVE_SUMMARY '):return
        orig_print(*items,**kw)
    live.print=local_print
    timeline=GCTimeline(disable_during_window=args.defer_gc,max_events=4096)
    timeline.__enter__()
    try:
        summary=live.run_live((root/'core.elf').read_bytes(),manifest['sha256']['core.elf'],(root/'core_config.txt').read_bytes(),evidence,seconds=args.seconds)
        saved['summary']=summary
        if b.fatal:
            print('HARDWARE_ACCESS_FROZEN '+b.fatal,flush=True);return 4
        saved['final']=b.finish();saved['summary']['pl_bridge']=b.report()
        saved['summary']['publisher_sequence_semantics']='Original core event/camera sequence preserved in rows; actual PL publisher session and sequence recorded in pl fields; resume uses strict new session'
        if args.software_loss_at is not None:assert b.loss and b.loss['resume'] is not None
        assert summary['status']=='COMPLETED' and b.accepted_fresh_count>0 and b.commits and not b.rejected and not b.fatal
        result='PASS'
    except Exception as e:
        traceback.print_exc();saved['error']=repr(e);result='FAIL'
        if not b.fatal and not b.closed:
            saved['failure_close']=b.close();b.monitor_stop.set();b.monitor_thread.join(timeout=3)
    finally:
        timeline.__exit__(None,None,None)
        saved['gc_timeline']=timeline.report()
        if b.closed and saved.get('summary',{}).get('capture',{}).get('capture_finalization_complete'):
            saved['gc_post_close_collection']=timeline.collect_after_close(hardware_closed=True)
    saved['result']=result
    raw=json.dumps(saved,allow_nan=False,separators=(',',':')).encode();packed=zlib.compress(raw,6)
    print('LIVE_PL_EXPORT_META '+json.dumps({'raw_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'packed_sha256':hashlib.sha256(packed).hexdigest(),'packed_bytes':len(packed)}),flush=True)
    enc=base64.b64encode(packed).decode()
    for i in range(0,len(enc),3072):print('LIVE_PL_EXPORT_B64 '+str(i//3072)+' '+enc[i:i+3072],flush=True)
    print('LIVE_PL_COMPLETE '+result,flush=True)
    return 0 if result=='PASS' else 3
if __name__=='__main__':raise SystemExit(main())
