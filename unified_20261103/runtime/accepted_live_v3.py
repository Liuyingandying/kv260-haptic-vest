"""RAM-only latest-frame V4L2 -> equivalence-gated ARM64 core -> quality view.

Import/run only from a RAM bundle after a reliable scale receipt exists. No SDK,
NumPy, MMIO, actuator, persistent target files, automatic restart or option writes.
"""
import base64, collections, hashlib, json, math, os, platform, re, select, struct, subprocess, zlib
import sys, threading, time
from pathlib import Path
from latest_slot import LatestSlot
from quality_bridge import CONFIG, annotate_cpp, form_kpf1_projection

SERIAL = '235523060782'
HEADER = struct.Struct('<4sIIIIIQQQdII')
CONFIG_SHA256 = 'f946eea0c578bf5410dbd184b05709f55ba8a8fd6e03338b7caf771a6124dcb0'
SCALE_PROOF_SHA256 = 'a3cb8ac20db2edf1267cd23e0af37e36914f09d27c3c41b0f06525bb9284a6f7'
CORE_IMAGE_SHA256 = '57c07c7e426a419414453d5159a1e60c635b06e7fa914ec775b8fb099011c12f'
USB_EVENTS = re.compile(r'usb[^\n]*(?:reset|disconnect|error|fail|timeout|timed out)|'
                        r'(?:xhci|uvcvideo)[^\n]*(?:error|fail|timeout|timed out|Non-zero status)', re.I)

def distribution(values):
    values = sorted(values)
    if not values: return dict(count=0, minimum=None, mean=None, p50=None, p95=None, maximum=None)
    def at(p):
        x=(len(values)-1)*p;lo,hi=math.floor(x),math.ceil(x)
        return values[lo]+(values[hi]-values[lo])*(x-lo)
    return dict(count=len(values), minimum=values[0], mean=sum(values)/len(values),
                p50=at(.5), p95=at(.95), maximum=values[-1])

def sched_snapshot(pid=None):
    """Read only at stage boundaries; do not enable kernel scheduler statistics."""
    tid=threading.get_native_id()
    path='/proc/'+str(pid)+'/schedstat' if pid is not None else '/proc/self/task/'+str(tid)+'/schedstat'
    result=dict(path=path,tid=pid if pid is not None else tid,status='UNKNOWN',
                scheduler_statistics_enabled=None,kernel_flag_status='UNKNOWN')
    try:
        value=Path('/proc/sys/kernel/sched_schedstats').read_text().strip()
        if value not in ('0','1'):raise ValueError('unexpected sched_schedstats value')
        result.update(scheduler_statistics_enabled=value=='1',kernel_flag_status='READABLE')
    except (OSError,ValueError) as exc:result['kernel_flag_error']=repr(exc)
    try:
        fields=Path(path).read_text().split()
        if len(fields)!=3:raise ValueError('expected three schedstat fields')
        cpu,wait,slices=map(int,fields)
        if min(cpu,wait,slices)<0:raise ValueError('negative schedstat counter')
        result.update(status='READABLE',raw_cpu_runtime_ns=cpu,raw_runqueue_wait_ns=wait,raw_timeslices=slices)
    except (OSError,ValueError) as exc:result['error']=repr(exc)
    return result

def sched_delta(before,after):
    result=dict(status='UNKNOWN',runqueue_wait_delta_ns=None,timeslices_delta=None,cpu_runtime_delta_ns=None,
                reason='KERNEL_SCHEDULER_STATISTICS_DISABLED_OR_UNAVAILABLE')
    if before.get('path')!=after.get('path') or before.get('status')!='READABLE' or after.get('status')!='READABLE':
        result['reason']='SCHEDSTAT_UNREADABLE_OR_IDENTITY_CHANGED';return result
    cpu=after['raw_cpu_runtime_ns']-before['raw_cpu_runtime_ns']
    if cpu>=0:result['cpu_runtime_delta_ns']=cpu
    if not (before.get('scheduler_statistics_enabled') is True and after.get('scheduler_statistics_enabled') is True):return result
    wait=after['raw_runqueue_wait_ns']-before['raw_runqueue_wait_ns']
    slices=after['raw_timeslices']-before['raw_timeslices']
    if min(cpu,wait,slices)<0:result['reason']='SCHEDSTAT_COUNTER_DECREASE';return result
    result.update(status='KNOWN',runqueue_wait_delta_ns=wait,timeslices_delta=slices,reason='SCHED_SCHEDSTATS_ENABLED_AT_BOTH_BOUNDARIES')
    return result

def thread_stage_profile(wall_start,cpu_start,before):
    after=sched_snapshot();cpu_end=time.thread_time_ns();wall_end=time.monotonic_ns()
    return dict(tid=threading.get_native_id(),wall_ns=wall_end-wall_start,thread_cpu_ns=cpu_end-cpu_start,
        blocked_or_descheduled_ns=(wall_end-wall_start)-(cpu_end-cpu_start),
        wall_minus_cpu_semantics='Includes blocking, GIL waiting and being descheduled; not isolated OS scheduling latency',
        schedstat_before=before,schedstat_after=after,schedstat_delta=sched_delta(before,after),
        scope='Stage boundaries only; includes boundary read overhead; excludes later evidence compression/export')

def checked_scale(evidence):
    """A scalar guess, model default or unverified SDK value cannot pass this gate."""
    if not isinstance(evidence, dict) or evidence.get('verified') is not True:
        raise RuntimeError('BLOCKED_UNVERIFIED_DEPTH_SCALE')
    scale = evidence.get('depth_scale_m')
    if evidence.get('serial') != SERIAL or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise RuntimeError('BLOCKED_SCALE_IDENTITY_OR_VALUE')
    if not evidence.get('method') or not re.fullmatch(r'[a-fA-F0-9]{64}', evidence.get('proof_sha256', '')):
        raise RuntimeError('BLOCKED_MISSING_SCALE_PROVENANCE')
    if (evidence['proof_sha256'] != SCALE_PROOF_SHA256 or evidence.get('firmware') != '5.13.0.55'
        or evidence.get('sdk_camera_serial') != '239222302427' or evidence.get('advanced_mode_enabled') is not True
        or evidence.get('serial_kind') != 'USB_iSerialNumber_and_GVD_ASIC'):
        raise RuntimeError('BLOCKED_SCALE_FIRMWARE_OR_ASIC_IDENTITY_PROOF')
    payload = bytes.fromhex(evidence.get('depth_payload_hex', ''))
    if (payload.hex() != 'e803000000000000000001000000000000000000' or len(payload) != 20
        or struct.unpack_from('<I', payload)[0] != evidence.get('depth_units_micrometers')):
        raise RuntimeError('BLOCKED_SCALE_RAW_PAYLOAD_MISMATCH')
    if not math.isclose(scale, evidence['depth_units_micrometers']*1e-6, rel_tol=0, abs_tol=1e-15):
        raise RuntimeError('BLOCKED_SCALE_MICROMETRE_CONVERSION_MISMATCH')
    return float(scale)

def ram_fd(name, data, executable=False):
    fd = os.memfd_create(name, os.MFD_CLOEXEC)
    try:
        remaining = memoryview(data)
        while remaining:
            n = os.write(fd, remaining)
            if n <= 0: raise RuntimeError('RAM_SHORT_WRITE')
            remaining = remaining[n:]
        os.lseek(fd, 0, os.SEEK_SET)
        if executable: os.fchmod(fd, 0o500)
        return fd
    except Exception:
        os.close(fd); raise

class CoreStream:
    def __init__(self, image, image_sha256, config):
        if hashlib.sha256(image).hexdigest() != image_sha256 or image_sha256 != CORE_IMAGE_SHA256:
            raise RuntimeError('CORE_IMAGE_SHA256_MISMATCH')
        if image[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', image, 18)[0] != 183:
            raise RuntimeError('CORE_NOT_AARCH64_ELF64_LE')
        if hashlib.sha256(config).hexdigest() != CONFIG_SHA256:
            raise RuntimeError('FROZEN_CONFIG_SHA256_MISMATCH')
        self.fds, self.proc = [], None
        self.stderr = collections.deque(maxlen=8192)
        self.pending = bytearray()
        try:
            elf = ram_fd('kv260-frozen-core', image, executable=True); self.fds.append(elf)
            cfg = ram_fd('kv260-frozen-config', config); self.fds.append(cfg)
            self.proc = subprocess.Popen(['/proc/self/fd/'+str(elf), '--stream', '--config', '/proc/self/fd/'+str(cfg), '--timing'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=tuple(self.fds), bufsize=0)
            os.set_blocking(self.proc.stdin.fileno(), False); os.set_blocking(self.proc.stdout.fileno(), False)
            def drain():
                for line in iter(self.proc.stderr.readline, b''):
                    self.stderr.append(line.decode(errors='replace').rstrip())
            self.reader = threading.Thread(target=drain, daemon=True); self.reader.start()
        except Exception:
            self.close(); raise

    def transact(self, packet, timeout=3.):
        began = time.monotonic_ns(); end = time.monotonic()+timeout; sent = 0
        phases = dict(pipe_input_wait_ns=0, pipe_input_write_ns=0, pipe_output_wait_ns=0, pipe_output_read_ns=0)
        while sent < len(packet):
            remaining = end-time.monotonic()
            if remaining <= 0: raise RuntimeError('CORE_INPUT_TIMEOUT')
            t=time.monotonic_ns()
            if not select.select([], [self.proc.stdin.fileno()], [], remaining)[1]:
                raise RuntimeError('CORE_INPUT_TIMEOUT')
            phases['pipe_input_wait_ns']+=time.monotonic_ns()-t;t=time.monotonic_ns()
            try: sent += os.write(self.proc.stdin.fileno(), memoryview(packet)[sent:])
            except BlockingIOError: pass
            phases['pipe_input_write_ns']+=time.monotonic_ns()-t
        while b'\n' not in self.pending:
            remaining = end-time.monotonic()
            if remaining <= 0: raise RuntimeError('CORE_OUTPUT_TIMEOUT')
            t=time.monotonic_ns()
            if not select.select([self.proc.stdout.fileno()], [], [], remaining)[0]:
                raise RuntimeError('CORE_OUTPUT_TIMEOUT')
            phases['pipe_output_wait_ns']+=time.monotonic_ns()-t;t=time.monotonic_ns()
            try: part = os.read(self.proc.stdout.fileno(), 65536)
            except BlockingIOError: continue
            phases['pipe_output_read_ns']+=time.monotonic_ns()-t
            if not part: raise RuntimeError('CORE_OUTPUT_EOF')
            self.pending.extend(part)
            if len(self.pending) > 1048576: raise RuntimeError('CORE_JSON_SIZE_LIMIT')
        line, _, rest = self.pending.partition(b'\n'); self.pending = bytearray(rest)
        t=time.monotonic_ns();decoded=json.loads(line)
        phases['json_parse_ns']=time.monotonic_ns()-t
        phases['core_transaction_ns']=time.monotonic_ns()-began
        return decoded, {k.removesuffix('_ns')+'_ms':v/1e6 for k,v in phases.items()}

    def close(self):
        errors = []
        if self.proc:
            try: self.proc.stdin.close()
            except Exception as exc: errors.append(repr(exc))
            try: self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try: self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=2)
                errors.append('OWN_CORE_PROCESS_TIMED_OUT_AND_TERMINATED')
            if self.proc.returncode != 0:
                errors.append('CORE_EXIT_NONZERO:'+str(self.proc.returncode))
            if hasattr(self, 'reader'):
                self.reader.join(timeout=1)
                if self.reader.is_alive():errors.append('STDERR_READER_DID_NOT_STOP')
            for pipe in (self.proc.stdout, self.proc.stderr):
                try: pipe.close()
                except Exception as exc: errors.append(repr(exc))
        for fd in self.fds:
            try: os.close(fd)
            except Exception as exc: errors.append(repr(exc))
        self.fds.clear()
        return errors

def kernel_phases(watch, boundary_ns):
    startup, active = [], []
    for line in watch.delta:
        if not USB_EVENTS.search(line): continue
        match = re.match(r'^\[\s*(\d+(?:\.\d+)?)\]', line)
        timestamp_ns = int(float(match[1])*1e9) if match else None
        if boundary_ns is None or timestamp_ns is not None and timestamp_ns < boundary_ns:
            startup.append(line)
        else:
            active.append(line)
    return startup, active

def compact_region_quality(envelope, packet, codec):
    """Current admitted UNKNOWN units are cells/side regions, never pixel counts."""
    fresh = bool(packet.flags & codec.FRESH)
    unknown = sum(codec.unpack_unknown(packet.unknown))
    raw = envelope['legacy_diagnostic']
    current = envelope['legacy_result']
    central = dict(coverage_ratio=current['central']['observed_ratio'] if fresh else None,
        coverage_available=fresh, raw_core_observed_ratio=raw['central']['observed_ratio'],
        unknown_count=unknown, region_count=256, unknown_fraction=unknown/256., unknown_unit='MATRIX_CELL',
        state='UNKNOWN' if unknown == 256 else 'PARTIAL_WITH_OBSERVATIONS' if unknown else 'COMPLETE_OBSERVATIONS',
        supported_candidate_cells=envelope['perception']['matrix_supported_candidate_cells'] if fresh else 0,
        supported_near_candidate_cells=envelope['perception']['matrix_supported_obstacle_cells'] if fresh else 0,
        rect_xyxy=current['roi'])
    result = dict(central=central)
    for name, side in (('left', packet.left), ('right', packet.right)):
        is_unknown = bool(side.flags & codec.UNKNOWN)
        source = envelope['perception']['sides'][name]
        result[name] = dict(coverage_ratio=current[name]['observed_ratio'] if fresh else None,
            coverage_available=fresh, raw_core_observed_ratio=raw[name]['observed_ratio'],
            unknown_count=int(is_unknown), region_count=1, unknown_fraction=float(is_unknown), unknown_unit='SIDE_REGION',
            state='UNKNOWN' if is_unknown else 'COMPLETE_OBSERVATIONS',
            supported_candidate=source['supported_candidate'] if fresh else False,
            supported_near_candidate=source['supported_obstacle_candidate'] if fresh else False,
            rect_xyxy=source['rect_xyxy'])
    return result

def collect_core_phases(records, lines, session):
    timings={};unparsed=[];duplicates=[]
    for line in lines:
        try:item=json.loads(line)
        except ValueError:unparsed.append(line);continue
        if not isinstance(item,dict):unparsed.append(line);continue
        if item.get('session')!=session or item.get('phase_profile_schema')!='basic_depth_v1_phase_profile_v1':
            unparsed.append(line);continue
        seq=item.get('seq')
        if seq in timings:duplicates.append(seq)
        timings[seq]={key:value for key,value in item.items() if key.endswith('_ms') and
                      isinstance(value,(int,float)) and math.isfinite(value) and value>=0}
    for row in records:row['core_reported_timing_ms']=timings.get(row['camera_sequence'],{})
    missing=[r['camera_sequence'] for r in records if not r['core_reported_timing_ms']]
    keys=set().union(*(r['core_reported_timing_ms'] for r in records)) if records else set()
    return dict(status='COMPLETE' if not missing and not duplicates else 'PARTIAL',missing_sequences=missing,
        duplicate_sequences=duplicates,per_phase_ms={key:distribution([r['core_reported_timing_ms'][key] for r in records
            if key in r['core_reported_timing_ms']]) for key in sorted(keys)},non_profile_stderr=unparsed,
        scope='Native stderr timings matched by unchanged source camera sequence; nested phases overlap')

def export_compressed(records, final_view):
    raw=json.dumps(dict(records=records,last_diagnostic=final_view),allow_nan=False,separators=(',',':')).encode()
    packed=zlib.compress(raw,6);encoded=base64.b64encode(packed).decode('ascii');size=3072
    meta=dict(schema='kv260_opt_live_export_v1',raw_bytes=len(raw),zlib_bytes=len(packed),base64_chars=len(encoded),
              raw_sha256=hashlib.sha256(raw).hexdigest(),zlib_sha256=hashlib.sha256(packed).hexdigest(),
              chunks=(len(encoded)+size-1)//size,records=len(records))
    print('KV260_OPT_EXPORT_META '+json.dumps(meta),flush=True)
    for index,start in enumerate(range(0,len(encoded),size)):
        print('KV260_OPT_EXPORT_B64 '+str(index)+' '+encoded[start:start+size],flush=True)
    print('KV260_OPT_EXPORT_DONE '+meta['raw_sha256'],flush=True)

def output_fields(envelope, packet, packet_bytes, codec):
    return dict(input_source='ACTUAL_V4L2_NATIVE_DEPTH', output_quality=int(packet.quality),
        frame_reason=('STALE_OR_FUTURE_AT_OUTPUT_ADMISSION' if int(packet.quality)==3 and
                      envelope['legacy_result']['frame_state']['fresh'] else envelope['legacy_result']['frame_state']['reason']),
        perception_state='UNUSABLE_AT_OUTPUT_ADMISSION' if int(packet.quality)==3 else envelope['perception']['state'],
        legacy_perception_state=envelope['perception']['state'],matrix=list(packet.matrix),
        unknown_lsb_first_hex=packet.unknown.hex(),unknown_cells=sum(codec.unpack_unknown(packet.unknown)),
        legacy_near_candidate_cells=envelope['perception']['matrix_supported_obstacle_cells'],
        side_unknown=[name for name,side in (('left',packet.left),('right',packet.right)) if side.flags&codec.UNKNOWN],
        legacy_side_unknown=envelope['perception']['unknown_sides'],kpf1_hex=packet_bytes.hex(),
        region_quality=compact_region_quality(envelope,packet,codec),physical_output_permitted=False,hardware_backend='NONE')

def capture(slot, state, seconds, stop):
    fd = None; maps = []; streaming = allocated = False; watch = None
    first_arrival = boundary_ns = previous = None
    samples = collections.defaultdict(list); setup_start=time.monotonic_ns()
    cpu_start=time.thread_time_ns();sched_before=sched_snapshot()
    try:
        import mmap
        import v4l2_support as vs
        class LiveKernelWatch(vs.KernelWatch):
            def poll(self, force=False):
                super().poll(force=force)
                self.usb_events[:] = [s for s in self.delta if USB_EVENTS.search(s)]
        state['identity'] = vs.camera_guard('/dev/video0')
        watch = LiveKernelWatch()
        if watch.status != 'READABLE' or watch.baseline_storage_errors:
            raise RuntimeError('KERNEL_OR_BASELINE_STORAGE_NOT_READY')
        fd = os.open('/dev/video0', os.O_RDWR|os.O_NONBLOCK)
        config = vs.configure(fd); state['config'] = config
        w, h, stride = config['width'], config['height'], config['bytesperline']
        needed = (h-1)*stride+w*2
        req = bytearray(20); struct.pack_into('<3I', req, 0, 4, 1, 1); vs.io(fd, 8, 20, req); allocated = True
        count = vs.uint(req, 0)
        if not 1 <= count <= 32: raise RuntimeError('BAD_BUFFER_COUNT')
        for i in range(count):
            b = vs.vbuffer(i); vs.io(fd, 9, 88, b)
            length, offset = vs.uint(b, 72), vs.uint(b, 64)
            if length < config['sizeimage']: raise RuntimeError('SHORT_MMAP_BUFFER')
            maps.append(mmap.mmap(fd, length, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ|mmap.PROT_WRITE, offset=offset))
            vs.io(fd, 15, 88, b)
        vs.io(fd, 18, 4, bytearray(struct.pack('<I', 1)), 1); streaming = True
        state['setup_ms']=(time.monotonic_ns()-setup_start)/1e6
        absolute = time.monotonic()+seconds+7
        while not stop.is_set():
            loop_ns=time.monotonic_ns();t=loop_ns;phases={}
            watch.poll()
            phases['kernel_poll_ms']=(time.monotonic_ns()-t)/1e6
            startup_usb, active_usb = kernel_phases(watch, boundary_ns)
            if watch.status != 'READABLE' or watch.failures: raise RuntimeError('KERNEL_MONITOR_UNKNOWN')
            if watch.storage_errors: raise RuntimeError('NEW_STORAGE_ERROR')
            if active_usb: raise RuntimeError('ACTIVE_USB_EVENT')
            end = min(absolute, time.monotonic()+5)
            if end <= time.monotonic(): raise RuntimeError('CAPTURE_ABSOLUTE_DEADLINE')
            t=time.monotonic_ns()
            if not select.select([fd], [], [], max(0, end-time.monotonic()))[0]: raise RuntimeError('CAPTURE_FRAME_TIMEOUT')
            phases['capture_select_wait_ms']=(time.monotonic_ns()-t)/1e6;t=time.monotonic_ns()
            b = vs.vbuffer()
            try: vs.io(fd, 17, 88, b)
            except BlockingIOError: continue
            arrival_ns = time.monotonic_ns()
            phases['dqbuf_ms']=(arrival_ns-t)/1e6
            i, used, flags, seq = vs.uint(b, 0), vs.uint(b, 8), vs.uint(b, 12), vs.uint(b, 56)
            sec, usec = struct.unpack_from('<qq', b, 24); capture_ns = (sec*1000000+usec)*1000
            if i >= len(maps): raise RuntimeError('BAD_BUFFER_INDEX')
            if flags & 0x40 or not needed <= used <= len(maps[i]):
                item = dict(sequence=seq, bytesused=used, flags=flags, capture_ns=capture_ns)
                if first_arrival is None and used == capture_ns == 0 and flags & 0x40:
                    state['startup_rejections'].append(item); vs.io(fd, 15, 88, b); continue
                state['active_rejections'].append(item); raise RuntimeError('ACTIVE_OR_UNEXPECTED_BUFFER_ERROR')
            if flags & 0xe000 != 0x2000 or sec < 0 or not 0 <= usec < 1000000:
                raise RuntimeError('V4L2_CAPTURE_CLOCK_NOT_MONOTONIC')
            if previous:
                if seq <= previous[0] or capture_ns <= previous[1]: raise RuntimeError('SOURCE_SEQUENCE_OR_CLOCK_NOT_INCREASING')
                state['source_sequence_gaps'] += max(0, seq-previous[0]-1)
            previous = (seq, capture_ns)
            if first_arrival is None: first_arrival, boundary_ns = arrival_ns, capture_ns
            t=time.monotonic_ns()
            raw = maps[i][:w*h*2] if stride == w*2 else b''.join(maps[i][y*stride:y*stride+w*2] for y in range(h))
            phases['mmap_copy_ms']=(time.monotonic_ns()-t)/1e6
            frame = dict(raw=raw, width=w, height=h, sequence=seq, capture_ns=capture_ns,
                         arrival_ns=arrival_ns, timestamp_flags=flags & 0x7e000, capture_phases_ms=dict(phases))
            done=(arrival_ns-first_arrival)/1e9 >= seconds
            t=time.monotonic_ns();slot.publish(frame,complete_boundary=done)
            phases['slot_publish_ms']=(time.monotonic_ns()-t)/1e6
            state['valid_captured_frames'] += 1
            state['first_capture_ns'] = state.get('first_capture_ns', capture_ns)
            state['last_capture_ns'] = capture_ns
            state['first_arrival_ns'] = state.get('first_arrival_ns', arrival_ns)
            state['last_arrival_ns'] = arrival_ns
            t=time.monotonic_ns()
            if not done: vs.io(fd, 15, 88, b)
            phases['qbuf_ms']=(time.monotonic_ns()-t)/1e6 if not done else 0.
            phases['capture_iteration_ms']=(time.monotonic_ns()-loop_ns)/1e6
            for key,value in phases.items():samples[key].append(value)
            if done:break
    except Exception as exc:
        state['errors'].append(repr(exc)); slot.close(error='SOURCE_FAULT: '+repr(exc))
    finally:
        if fd is not None:
            if streaming:
                try: vs.io(fd, 19, 4, bytearray(struct.pack('<I', 1)), 1)
                except Exception as exc: state['cleanup_errors'].append('STREAMOFF: '+repr(exc))
            for m in maps:
                try: m.close()
                except Exception as exc: state['cleanup_errors'].append('MMAP: '+repr(exc))
            if allocated:
                try:
                    req = bytearray(20); struct.pack_into('<3I', req, 0, 0, 1, 1); vs.io(fd, 8, 20, req)
                except Exception as exc: state['cleanup_errors'].append('REQBUFS_FREE: '+repr(exc))
            try:os.close(fd)
            except Exception as exc:state['cleanup_errors'].append('CLOSE: '+repr(exc))
        if watch:
            watch.poll(force=True)
            startup_usb, active_usb = kernel_phases(watch, boundary_ns)
            state.update(kernel=watch.report(), startup_usb_events=startup_usb, active_usb_events=active_usb)
            if active_usb or watch.storage_errors or watch.status != 'READABLE' or watch.failures:
                slot.close(error='SOURCE_FAULT_POST_CAPTURE_KERNEL')
        if state['cleanup_errors']: slot.close(error='SOURCE_CLEANUP_ERROR')
        state['source_phase_ms']={key:distribution(value) for key,value in samples.items()}
        state['source_phase_scope']='VALID_DEQUEUED_FRAMES_ONLY; source and consumer timings overlap across threads'
        state['capture_thread_timing']=thread_stage_profile(setup_start,cpu_start,sched_before)
        state['capture_finalization_complete']=True
        slot.close()

def run_live(core_image, core_sha256, config_bytes, scale_evidence, seconds=10):
    """Inputs must already reside in caller-owned RAM; this function writes no files."""
    scale = checked_scale(scale_evidence)  # Before opening camera or starting metric core.
    if platform.system() != 'Linux' or platform.machine() != 'aarch64': raise RuntimeError('REQUIRES_ACTUAL_KV260_AARCH64')
    if seconds not in (10, 60): raise RuntimeError('DURATION_MUST_BE_10_OR_60')
    import kpf1_codec as codec
    session = time.monotonic_ns()//1000000
    if not 1 <= session <= 0xffffffff: raise RuntimeError('SESSION_REQUIRES_EXPLICIT_NEW_EPOCH')
    slot, stop = LatestSlot(), threading.Event()
    state = dict(startup_rejections=[], active_rejections=[], errors=[], cleanup_errors=[],
                 source_sequence_gaps=0, valid_captured_frames=0,
                 startup_usb_events=[], active_usb_events=[], kernel={'status':'UNKNOWN'})
    core = CoreStream(core_image, core_sha256, config_bytes)
    worker = threading.Thread(target=capture, args=(slot, state, seconds, stop), daemon=True)
    records, final_view, event_sequence, first_dispatch, last_completed = [], None, 0, None, None
    errors = []
    consumer_wall_start=time.monotonic_ns();consumer_cpu_start=time.thread_time_ns()
    consumer_sched_before=sched_snapshot();core_sched_before=sched_snapshot(core.proc.pid)
    worker.start()
    consumer_deadline = time.monotonic()+seconds+12
    try:
        while True:
            if time.monotonic() >= consumer_deadline: raise RuntimeError('CONSUMER_ABSOLUTE_DEADLINE')
            take_began=time.monotonic_ns()
            frame = slot.take(timeout=.5)
            take_completed=time.monotonic_ns()
            if frame is None:
                if slot.snapshot()['closed']: break
                if not worker.is_alive():
                    slot.close(error='CAPTURE_THREAD_EXITED_WITHOUT_CLOSE')
                    raise RuntimeError('CAPTURE_THREAD_EXITED_WITHOUT_CLOSE')
                continue
            if len(records) >= seconds*30+8: raise RuntimeError('BOUNDED_RECORD_CAPACITY')
            dispatch = time.monotonic_ns()
            cpu_dispatch=time.thread_time_ns()
            if first_dispatch is None: first_dispatch = dispatch
            t=time.monotonic_ns()
            header = HEADER.pack(b'BDP1', frame['width'], frame['height'], frame['width']*2,
                                 2, session, frame['sequence'], frame['capture_ns'], dispatch, scale, 1, 0)
            bdp=header+frame['raw'];bdp_ms=(time.monotonic_ns()-t)/1e6
            legacy,io_phases = core.transact(bdp)
            completed = time.monotonic_ns(); last_completed = completed
            if any(legacy.get(k) != v for k, v in (('session', session), ('seq', frame['sequence']),
                    ('capture_ns', frame['capture_ns']), ('width', frame['width']), ('height', frame['height']),
                    ('coordinate_space', 'native_depth'), ('source_kind', 'Z16_RAW'))):
                raise RuntimeError('CORE_FRAME_IDENTITY_MISMATCH')
            identity_ms=(time.monotonic_ns()-completed)/1e6;t=time.monotonic_ns()
            envelope = annotate_cpp(legacy, now_ns=completed, source_fault=slot.snapshot()['error'])
            quality_ns=time.monotonic_ns()-t;packet_ns=row_ns=0;rebuild_ns=0;lock_began=time.monotonic_ns()
            with slot.condition:
                lock_wait_ms=(time.monotonic_ns()-lock_began)/1e6
                if slot.error:
                    t=time.monotonic_ns()
                    envelope = annotate_cpp(legacy, now_ns=time.monotonic_ns(), source_fault=slot.error)
                    quality_ns+=time.monotonic_ns()-t
                event_sequence += 1; admission_ns = time.monotonic_ns()
                t=time.monotonic_ns()
                packet,packet_bytes = form_kpf1_projection(envelope, codec, admission_ns, event_sequence)
                packet_ns+=time.monotonic_ns()-t;t=time.monotonic_ns()
                fields=output_fields(envelope,packet,packet_bytes,codec)
                row_ns+=time.monotonic_ns()-t
                cpu_ready=time.thread_time_ns()
                matrix_ready_ns = time.monotonic_ns()
                if packet.flags & codec.FRESH and not -40. <= (matrix_ready_ns-frame['capture_ns'])/1e6 <= 250.:
                    rebuild_began=time.monotonic_ns();t=rebuild_began
                    envelope = annotate_cpp(legacy, now_ns=matrix_ready_ns, source_fault=slot.error)
                    quality_ns+=time.monotonic_ns()-t;t=time.monotonic_ns()
                    packet,packet_bytes = form_kpf1_projection(envelope, codec, matrix_ready_ns, event_sequence)
                    packet_ns+=time.monotonic_ns()-t;t=time.monotonic_ns()
                    fields=output_fields(envelope,packet,packet_bytes,codec)
                    row_ns+=time.monotonic_ns()-t
                    cpu_ready=time.thread_time_ns()
                    matrix_ready_ns = time.monotonic_ns()
                    rebuild_ns=matrix_ready_ns-rebuild_began
                    if packet.quality != codec.Quality.UNUSABLE or any(packet.matrix) or packet.unknown != b'\xff'*32:
                        raise RuntimeError('FINAL_READY_AGE_GATE_FAILED_CLOSED')
                if packet.session != session or packet.sequence != event_sequence or packet.capture_ns != frame['capture_ns']:
                    raise RuntimeError('PUBLISHER_OR_CAPTURE_IDENTITY_MISMATCH')
            # Only packet admission is called current; original near evidence remains diagnostic.
            row = dict(camera_sequence=frame['sequence'], publisher_event_sequence=event_sequence,
                session=session, capture_ns=frame['capture_ns'], arrival_ns=frame['arrival_ns'], dispatch_ns=dispatch,
                core_completed_ns=completed, output_admission_ns=admission_ns, matrix_ready_ns=matrix_ready_ns,
                output_age_ms=(matrix_ready_ns-frame['capture_ns'])/1e6,
                frame_wait_ms=(dispatch-frame['arrival_ns'])/1e6,
                cpp_roundtrip_ms=(completed-dispatch)/1e6,
                quality_bridge_ms=(admission_ns-completed)/1e6,
                post_cpp_to_matrix_ms=(matrix_ready_ns-completed)/1e6,
                packet_formation_ms=(matrix_ready_ns-admission_ns)/1e6,
                full_processing_ms=(matrix_ready_ns-dispatch)/1e6,
                phases_ms=dict(io_phases,slot_take_wait_ms=(take_completed-take_began)/1e6,
                    dispatch_scheduling_ms=(dispatch-take_completed)/1e6,bdp_assembly_ms=bdp_ms,
                    identity_validation_ms=identity_ms,quality_projection_ms=quality_ns/1e6,
                    source_lock_wait_ms=lock_wait_ms,packet_encode_ms=packet_ns/1e6,row_statistics_ms=row_ns/1e6,
                    final_gate_rebuild_ms=rebuild_ns/1e6),capture_phases_ms=frame['capture_phases_ms'],
                final_gate_rebuilt=bool(rebuild_ns),**fields)
            row.update(consumer_thread_cpu_ns=cpu_ready-cpu_dispatch,consumer_wall_ns=matrix_ready_ns-dispatch,
                blocked_or_descheduled_ns=(matrix_ready_ns-dispatch)-(cpu_ready-cpu_dispatch),
                consumer_native_tid=threading.get_native_id())
            row['phases_ms'].update(consumer_thread_cpu_ms=row['consumer_thread_cpu_ns']/1e6,
                blocked_or_descheduled_ms=row['blocked_or_descheduled_ns']/1e6,
                post_take_clock_gap_ms=row['phases_ms']['dispatch_scheduling_ms'])
            accounted=(bdp_ms+io_phases['core_transaction_ms']+identity_ms+
                       (quality_ns+packet_ns+row_ns)/1e6+lock_wait_ms)
            row['phases_ms']['consumer_unaccounted_ms']=max(0.,row['full_processing_ms']-accounted)
            records.append(row); final_view = envelope
    except Exception as exc:
        errors.append(repr(exc)); slot.close(error='PROCESSING_FAULT: '+repr(exc))
    finally:
        stop.set(); worker.join(timeout=8)
        if worker.is_alive(): errors.append('CAPTURE_THREAD_DID_NOT_STOP')
        if not state.get('capture_finalization_complete'):errors.append('CAPTURE_FINALIZATION_INCOMPLETE')
        core_sched_after=sched_snapshot(core.proc.pid)
        consumer_stage_timing=thread_stage_profile(consumer_wall_start,consumer_cpu_start,consumer_sched_before)
        cleanup = core.close()
    if state['errors'] or slot.snapshot()['error']: errors.append('SOURCE_OR_SLOT_FAULT')
    native_phases=collect_core_phases(records,list(core.stderr),session)
    duration = (records[-1]['matrix_ready_ns']-records[0]['matrix_ready_ns'])/1e9 if len(records) >= 2 else None
    work_duration = (records[-1]['matrix_ready_ns']-first_dispatch)/1e9 if records else None
    capture_window = (state['last_arrival_ns']-state['first_arrival_ns'])/1e9 if state.get('first_arrival_ns') is not None else 0.
    state['captured_window_seconds'] = capture_window
    state['actual_capture_fps'] = (state['valid_captured_frames']-1)/capture_window if capture_window > 0 else None
    clean_capture = (capture_window >= seconds and state['valid_captured_frames'] >= 2 and not state['errors']
                     and not state['active_usb_events'] and not state['active_rejections'] and not state['source_sequence_gaps']
                     and not state['cleanup_errors'] and not slot.snapshot()['error'])
    def row_distribution(key):
        values = sorted(r[key] for r in records)
        if not values: return dict(p50=None, p95=None, maximum=None)
        def at(p):
            x = (len(values)-1)*p; lo, hi = math.floor(x), math.ceil(x)
            return values[lo]+(values[hi]-values[lo])*(x-lo)
        return dict(p50=at(.5), p95=at(.95), maximum=values[-1])
    summary = dict(status='COMPLETED' if records and clean_capture and not errors and not cleanup else 'BLOCKED',
        acquisition_status=('FAIL' if not clean_capture else
                            'PASS_STARTUP_WARN' if state['startup_usb_events'] or state['startup_rejections'] else 'PASS'),
        metric_conversion_units_source_verified=True, physical_distance_accuracy_verified=False,
        depth_scale_m=scale, scale_evidence=scale_evidence, frozen_core_sha256=core_sha256,
        frozen_config_sha256=CONFIG_SHA256, core_config=CONFIG, requested_seconds=seconds,
        processed_frames=len(records), matrix_ready_span_seconds=duration,
        processing_output_fps=(len(records)-1)/duration if duration else None,
        processing_work_elapsed_seconds=work_duration,
        processing_throughput_fps=len(records)/work_duration if work_duration else None,
        processing_timing_ms={key:row_distribution(key) for key in ('cpp_roundtrip_ms', 'quality_bridge_ms',
            'packet_formation_ms', 'post_cpp_to_matrix_ms', 'full_processing_ms', 'output_age_ms', 'frame_wait_ms')},
        consumer_phase_ms={key:distribution([r['phases_ms'][key] for r in records]) for key in
                           (records[0]['phases_ms'] if records else [])},
        timing_revision=2,consumer_thread_timing=consumer_stage_timing,
        lifecycle_revision=3,
        normal_completion_policy='Atomic normal close discards unstarted pending at acquisition boundary; in-flight final age gate unchanged',
        child_main_thread_schedstat=dict(before=core_sched_before,after=core_sched_after,
            delta=sched_delta(core_sched_before,core_sched_after)),
        per_frame_cpu_clock='time.thread_time_ns: consumer thread only; excludes separate C++ process CPU',
        blocked_or_descheduled_semantics='Consumer wall minus thread CPU; includes pipe waits/GIL/descheduling; never isolated scheduler latency',
        dispatch_scheduling_ms_semantics='Legacy key: elapsed clock gap from slot take completion to dispatch; not OS scheduling delay',
        scheduler_measurement_policy='Read-only stage-boundary schedstat; runqueue UNKNOWN if sched_schedstats disabled/unavailable; no per-frame proc reads',
        final_age_gate_rebuilds=sum(r['final_gate_rebuilt'] for r in records),
        stale_or_unusable_output_count=sum(r['output_quality']==3 for r in records),
        timing_scope='Wall-clock spans; core_transaction includes its pipe/JSON subphases; final_gate_rebuild overlaps quality/packet/row phases',
        redundant_target_packet_decode=False,
        output_age_ms=[r['output_age_ms'] for r in records], slot=slot.snapshot(), capture=state,
        output_quality_counts={str(q):sum(r['output_quality'] == q for r in records) for q in range(4)},
        errors=errors, core_cleanup_errors=cleanup, core_stderr=native_phases['non_profile_stderr'],
        core_native_phase_profile={key:value for key,value in native_phases.items() if key!='non_profile_stderr'},
        raw_core_last_diagnostic_retained_in_ram=final_view is not None,
        raw_core_diagnostic_field='KV260_LIVE_LAST_DIAGNOSTIC.legacy_diagnostic',
        source_camera_sequence_may_be_zero=True,
        publisher_sequence_semantics='positive local event index for RAM KPF1 debug only; camera_sequence unchanged; no MMIO',
        physical_output_permitted=False, pending_writes=0, commit_writes=0,
        full_v11_annotation_equivalence=False, missing_detail='FIVE_DISJOINT_PIXEL_CATEGORIES_PER_REGION',
        result_scope='RECORDED_CURRENT_RUN_RESULTS; not an actuator or current-at-export freshness claim')
    print('KV260_LIVE_SUMMARY '+json.dumps(summary, allow_nan=False), flush=True)
    export_compressed(records,final_view)
    print('KV260_LIVE_END '+summary['status'], flush=True)
    return summary

if __name__ == '__main__':
    # A RAM bootstrap must explicitly call run_live with reliable scale provenance.
    print('BLOCKED: callable RAM bridge prepared; no verified scale/core bundle supplied.', flush=True)
    raise SystemExit(3)
