"""Offline KV260 D455 Z16 window. All target data stays in RAM until stdout export.

The embedded AArch64 statistics function has no syscalls or library dependencies.
No SDK, NumPy, persistent files, camera controls, firmware, or actuator access.
"""
import base64, ctypes, errno, fcntl, hashlib, json, math, mmap, os
import pathlib, platform, re, select, struct, subprocess, sys, time, zlib
from fractions import Fraction

DURATION_S = 10                 # Root may set to 60 only after the 10-second gate passes.
DEVICE = '/dev/video0'
SERIAL = '235523060782'
WIDTH, HEIGHT, FPS = 848, 480, 30
GRID_4X4 = False                # The two requested ROIs are always measured.
FRAME_TIMEOUT_S = 5.0
KERNEL_POLL_S = 2.0
Z16 = 0x2036315a
NATIVE_ZLIB_BASE64 = 'eNrt1FtsFFUYwPFvZnZ7oUWXTVuaAm0lKBTMpggoKISFIoRoCmyXB8PD2htW11JpV1Muhq0lxQQfKHIJGFl4IZTEhDc1MYGqMRhfiAj6Ii0aAYPGKOGyCIxnds9Id+QWY3z6/5Ltt+fM+eZ855vpbnr2+cWmYYjLkg/l9kgkrGNyxGxYZkue+jtaijOzfrm74rzcKPo+zl9nqtbOztbaRk48oVefkNw88z55R/RqNxbouPynRIvPmdfFemOJ5EafjitUXp48uICOEb3fg57P7a6pv7v9EV8g5/rOyJcn7rW/sy7oVG9mxzssfWH99CdjXYnGRFfu+iqR3zeXh69Xj5foYp+s+NWSw79Z1tRq9f1HuzcyJFbc3Fa3fso4idrGorXTuqsamvqXrT9UIdGbGz4ZOGf5DifF36+2XHbTkv5LDcbA5VXmwNVxkq6qtk4d8/XMHRK5OGjb0bpis+jgnj2Rvj0fd4Z9ZlHcMIpCG42Gmo0H3xu10WxoVnHYTn0df9NsOKfqqLnZdLJBXW+70Xwyqq5PV5+AJCPJYkmn9kt6kxmID29JVVQZgfit3amKSqkc1VooM1Nq/owlweDmZEVMXYv0ba/4xRp92FK5l+1UJPJGZcOE7KPeNiyTK3uSn8412wbnmb7yNY9s/qG+J/nW3Pmmlbn3kGHFRd1nk45lsnqf6nBRs0/G5E2SMdIiY94plP4t3auvnZeyrVu6H7vW1/3itS980t/XXX1tvupf4SiJ9hWH02Vqvy1GeE7SH54z+MrgvKvVMtU2Ze2Vsam95/NTFUk5Nm+wsrx+V0E4bZular+z9afLdu3dVSDppCTnDVU/dXGpSHS3On8y4KyR7Dgg6fmqvrfTdtQ5j1Nz4XU7eittz3TO2JP8zHO+3rnHbXuss5/Tl161X77qzT6/zLqi+qNenRKn3lKV69TqnG/72djsA6rmyESZGlM1D1vBwxPHH6i/rGqPmWXxA6ru5pLevb2qVqfubL1PZOrbrurtU/UOSfDiQmecqTc//rP6rt6/tcP5qb2ZZzC+vN7t/3nV3/mSHz/+px0N5MmYD4yHt8YkEK/Jy/ayR9V/VdVaqGp1ckfmOfU6uUtv2VH3HNt1350zrFRnOKr7/ofqu3OOo+bYuNP7OnNypXuOJtWv22eZlTnLTt37KinNnGWnPssFVafT6wUq3+n1kJobUHO9qvcT1X0add+/U3VfalgwIM67uGqh+j8Jp29u+PzQMSt2Ws0Fs1E8v3oj4jjrmRI9Xn7Dth/3/O4Y6yPi6640xhVXiXOtXH3Gq3WZ34HlD4XfL9iXv+TdvB3+nb5d1m7T/OrM99+eOn3ym4cEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD8W5dv2R1O3GZkx349f0GP8/T4Iz0epcdFnlj+P9Vbaxv3vL6kru7p6ilL6lfWVE+fEZoZqhUJdbV1JToTjU0SemnN66G2xq42CbWsW9O1rj0bE50SSrR2JyTU2dHSmGiUUGtbbHVnY3trrK2l8/Yos7qx/eVmdaOOROZP6LVX1Zfmjvb21jWJ/+J8Tj+v2HaHqcfuc3HjJD3vXi/w5Jfqe5ie5+bGWj1vqY8xIt99fhP0NdPzHrixyLOf92k8qufc9+bvG+m4w8pdH/DkT9P5QT3e78+N3vUBT5yh891tj/hzY4kn3+cZL/DkH/fnxkWe9d7+P6fPbnne11pb1zGi/yPz3fqjen/3+rDOH7bvvF+BJ77gyb+g8y/Yd/4/9eY36fzaf7yZ2fsE7/L8R0brDu91UK+I3+f9+QumL8vc'
NATIVE_SHA256 = '1ac22bcd5a57db1fc3204f7e0324ea109d9144ff0f5509f7e483eb142feaf8f0'
STORAGE = re.compile(r'mmc[^\n]*(?:I/O error|error -\d+|timed out|CRC error|data error)|'
                     r'(?:I/O error|Buffer I/O error|blk_update_request|end_request)[^\n]*mmcblk|'
                     r'mmcblk[^\n]*(?:I/O error|read failed|write failed)|EXT4-fs error|'
                     r'Aborting journal|JBD2[^\n]*(?:error|aborting)|Remounting filesystem read-only', re.I)
USB = re.compile(r'usb[^\n]*(?:reset|disconnect|error|fail|timeout|timed out)|'
                 r'(?:xhci|uvcvideo)[^\n]*(?:error|fail|timeout|timed out)', re.I)

def io(fd, number, size, buffer, direction=3):
    fcntl.ioctl(fd, (direction << 30) | (size << 16) | (86 << 8) | number, buffer, True)

def uint(buffer, offset):
    return struct.unpack_from('<I', buffer, offset)[0]

def enumeration(fd, number, size, args):
    for index in range(512):
        b = bytearray(size)
        struct.pack_into('<' + 'I' * (len(args)+1), b, 0, index, *args)
        try:
            io(fd, number, size, b)
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                return
            raise
        yield b

def vbuffer(index=0):
    b = bytearray(88)
    struct.pack_into('<II', b, 0, index, 1)
    struct.pack_into('<I', b, 60, 1)
    return b

def camera_guard(node):
    if platform.system() != 'Linux' or platform.machine() != 'aarch64' or sys.byteorder != 'little' or struct.calcsize('P') != 8:
        raise RuntimeError('REQUIRES_LINUX_AARCH64_LE64')
    path = pathlib.Path('/sys/class/video4linux', pathlib.Path(node).name, 'device').resolve()
    for parent in (path, *path.parents):
        try:
            if all((parent/key).read_text().strip() == value for key, value in
                   (('idVendor', '8086'), ('idProduct', '0b5c'), ('serial', SERIAL))):
                speed = (parent/'speed').read_text().strip()
                if float(speed) < 5000:
                    raise RuntimeError('USB3_SPEED_NOT_CONFIRMED')
                return dict(usb=str(parent), usb_vid='8086', usb_pid='0b5c', serial=SERIAL,
                            usb_speed_mbps=speed, platform_uname=platform.uname()._asdict())
        except OSError:
            pass
    raise RuntimeError('USB_ID_MISMATCH')

def native_loader():
    blob = zlib.decompress(base64.b64decode(NATIVE_ZLIB_BASE64, validate=True))
    if hashlib.sha256(blob).hexdigest() != NATIVE_SHA256:
        raise RuntimeError('NATIVE_STATISTICS_SHA256_MISMATCH')
    fd = os.memfd_create('kv260-z16-statistics', os.MFD_CLOEXEC)
    try:
        remaining = memoryview(blob)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise RuntimeError('MEMFD_SHORT_WRITE')
            remaining = remaining[written:]
        lib = ctypes.CDLL('/proc/self/fd/' + str(fd))
        fn = lib.z16_stats
        fn.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint32]
        fn.restype = ctypes.c_int
        return fd, lib, fn
    except Exception:
        os.close(fd)
        raise

class KernelWatch:
    def __init__(self):
        self.baseline_uptime = float(pathlib.Path('/proc/uptime').read_text().split()[0])
        self.status, self.failures, self.delta = 'UNKNOWN', [], []
        self.usb_events, self.storage_errors = [], []
        self.last_poll = 0.0
        self.before = self.read()
        self.after = self.before
        self.seen = set(self.before.splitlines())
        self.baseline_storage_errors = [s for s in self.before.splitlines() if STORAGE.search(s)]

    def read(self):
        try:
            r = subprocess.run(['dmesg', '--color=never'],
                               capture_output=True, text=True, timeout=2)
            if r.returncode:
                raise RuntimeError(r.stderr.strip() or 'dmesg failed')
            self.status = 'READABLE'
            return r.stdout
        except Exception as exc:
            self.status = 'UNKNOWN'
            error = repr(exc)
            if error not in self.failures:
                self.failures.append(error)
            return ''

    def poll(self, force=False):
        if not force and time.monotonic()-self.last_poll < KERNEL_POLL_S:
            return
        self.last_poll = time.monotonic()
        raw = self.read()
        if self.status != 'READABLE':
            return
        self.after = raw
        for line in raw.splitlines():
            match = re.match(r'^\[\s*(\d+(?:\.\d+)?)\]', line)
            if line in self.seen or not match or float(match[1]) < self.baseline_uptime:
                continue
            self.seen.add(line)
            self.delta.append(line)
            if STORAGE.search(line):
                self.storage_errors.append(line)
            if USB.search(line):
                self.usb_events.append(line)

    def report(self):
        return dict(status=self.status, baseline_uptime=self.baseline_uptime,
                    baseline_storage_errors=self.baseline_storage_errors,
                    new_storage_errors=self.storage_errors, new_usb_events=self.usb_events,
                    new_kernel_lines=self.delta, failures=self.failures,
                    dmesg_before=self.before, dmesg_after=self.after,
                    poll_seconds=KERNEL_POLL_S, usb_scope='all new kernel USB/UVC/XHCI events')

def configure(fd):
    c = bytearray(104); io(fd, 0, 104, c, 2)
    caps = uint(c, 88) if uint(c, 84) & 0x80000000 else uint(c, 84)
    if not caps & 1 or not caps & 0x04000000:
        raise RuntimeError('NOT_SINGLEPLANE_STREAMING')
    if not any(uint(b, 44) == Z16 for b in enumeration(fd, 2, 64, [1])):
        raise RuntimeError('Z16_NOT_ENUMERATED')
    sizes = []
    for b in enumeration(fd, 74, 44, [Z16]):
        kind = uint(b, 8)
        values = struct.unpack_from('<6I', b, 12)
        sizes.append(dict(type=kind, values=list(values[:2] if kind == 1 else values)))
    supported = False
    for entry in sizes:
        v = entry['values']
        if entry['type'] == 1:
            supported |= tuple(v) == (WIDTH, HEIGHT)
        else:
            x, X, dx, y, Y, dy = v
            supported |= x <= WIDTH <= X and y <= HEIGHT <= Y and (WIDTH-x) % max(1, dx) == 0 and (HEIGHT-y) % max(1, dy) == 0
    if not supported:
        raise RuntimeError('ACTUAL_848_480_Z16_SIZE_NOT_SUPPORTED: ' + repr(sizes))
    intervals, desired, matched = [], Fraction(1, FPS), False
    for b in enumeration(fd, 75, 52, [Z16, WIDTH, HEIGHT]):
        kind = uint(b, 16); v = struct.unpack_from('<6I', b, 20)
        intervals.append(dict(type=kind, values=list(v[:2] if kind == 1 else v)))
        if kind == 1:
            matched |= v[1] > 0 and Fraction(v[0], v[1]) == desired
        elif v[1] and v[3]:
            low, high = Fraction(v[0], v[1]), Fraction(v[2], v[3])
            step = Fraction(v[4], v[5]) if v[5] else Fraction(0)
            matched |= low <= desired <= high and (kind == 2 or step > 0 and (desired-low) % step == 0)
    if not matched:
        raise RuntimeError('ACTUAL_30FPS_INTERVAL_NOT_ENUMERATED: ' + repr(intervals))
    f = bytearray(208); struct.pack_into('<I', f, 0, 1)
    struct.pack_into('<4I', f, 8, WIDTH, HEIGHT, Z16, 1); io(fd, 5, 208, f)
    f = bytearray(208); struct.pack_into('<I', f, 0, 1); io(fd, 4, 208, f)
    w, h, pix, field, stride, sizeimage = struct.unpack_from('<6I', f, 8)
    if (w, h, pix) != (WIDTH, HEIGHT, Z16) or stride < w*2 or sizeimage < (h-1)*stride+w*2:
        raise RuntimeError('ACTIVE_FORMAT_MISMATCH')
    parm = bytearray(204); struct.pack_into('<I', parm, 0, 1)
    struct.pack_into('<II', parm, 12, 1, FPS); io(fd, 22, 204, parm)
    parm = bytearray(204); struct.pack_into('<I', parm, 0, 1); io(fd, 21, 204, parm)
    numerator, denominator = struct.unpack_from('<II', parm, 12)
    if not denominator or Fraction(numerator, denominator) != desired:
        raise RuntimeError('ACTIVE_FRAMERATE_MISMATCH: ' + repr((numerator, denominator)))
    return dict(width=w, height=h, fourcc='Z16 ', bytesperline=stride, sizeimage=sizeimage,
                requested_fps=FPS, actual_timeperframe=[numerator, denominator],
                enumerated_z16_sizes=sizes, enumerated_intervals=intervals,
                roi_pixels={'left_outer': [0, w//5, 0, h],
                            'central_left_lower': [w//5, w//2, h//2, h*9//10]},
                grid_4x4_enabled=GRID_4X4)

def quantiles(values):
    if not values:
        return dict(count=0, p50=None, p95=None, maximum=None)
    ordered = sorted(values)
    def at(p):
        x = (len(ordered)-1)*p; low, high = math.floor(x), math.ceil(x)
        return ordered[low] + (ordered[high]-ordered[low])*(x-low)
    return dict(count=len(ordered), p50=at(.5), p95=at(.95), maximum=ordered[-1])

def ratios(values):
    return dict(mean=sum(values)/len(values), minimum=min(values), maximum=max(values)) if values else None

def export_samples(samples):
    for tag in ('first', 'middle', 'last'):
        if tag not in samples:
            continue
        raw, frame = samples[tag]
        meta = dict(tag=tag, frame=frame, width=WIDTH, height=HEIGHT, dtype='<u2', layout='packed_row_major',
                    raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest(),
                    raw_exported=tag == 'last')
        if tag != 'last':
            meta.update(encoding='SHA256_ONLY_NO_RAW_EXPORT', chunks=0)
            print('V4L2_CONT_SAMPLE_META ' + json.dumps(meta), flush=True)
            print('V4L2_CONT_SAMPLE_END %s %s' % (tag, meta['raw_sha256']), flush=True)
            continue
        compressed = zlib.compress(raw, 1); encoded = base64.b64encode(compressed).decode()
        meta.update(zlib_bytes=len(compressed), zlib_sha256=hashlib.sha256(compressed).hexdigest(),
                    encoding='base64(zlib(packed_Z16))', base64_chars=len(encoded), chunk_chars=3072,
                    chunks=(len(encoded)+3071)//3072)
        print('V4L2_CONT_SAMPLE_META ' + json.dumps(meta), flush=True)
        for index, start in enumerate(range(0, len(encoded), 3072)):
            print('V4L2_CONT_B64 %s %05d %s' % (tag, index, encoded[start:start+3072]), flush=True)
        print('V4L2_CONT_SAMPLE_END %s %s' % (tag, meta['raw_sha256']), flush=True)

def export_frames(rows):
    raw = b''.join(json.dumps(row, separators=(',', ':'), allow_nan=False).encode()+b'\n' for row in rows)
    compressed = zlib.compress(raw, 1); encoded = base64.b64encode(compressed).decode()
    meta = dict(rows=len(rows), raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest(),
                zlib_bytes=len(compressed), zlib_sha256=hashlib.sha256(compressed).hexdigest(),
                base64_chars=len(encoded), chunk_chars=3072, chunks=(len(encoded)+3071)//3072,
                encoding='base64(zlib(JSONL_UTF8))')
    print('V4L2_CONT_FRAMES_META '+json.dumps(meta), flush=True)
    for index, start in enumerate(range(0, len(encoded), 3072)):
        print('V4L2_CONT_FRAMES_B64 %05d %s' % (index, encoded[start:start+3072]), flush=True)
    print('V4L2_CONT_FRAMES_END '+meta['raw_sha256'], flush=True)

def main():
    if DURATION_S not in (10, 60):
        raise RuntimeError('DURATION_MUST_BE_10_OR_60_SECONDS')
    fd = native_fd = None; maps = []; anchors = []; addresses = []
    streaming = allocated = False; rows = []; samples = {}; errors = []; cleanup = []
    config, identity, kernel = {}, {}, None
    rejected = []; startup_rejections = []; previous = None; first_valid = last_valid = None
    started = time.monotonic()
    try:
        identity = camera_guard(DEVICE)
        kernel = KernelWatch()
        if kernel.baseline_storage_errors:
            raise RuntimeError('BASELINE_STORAGE_ERROR_STOP')
        native_fd, native_lib, stats = native_loader()
        native_out = (ctypes.c_uint64 * 41)()
        fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
        config = configure(fd)
        w, h, stride, size = config['width'], config['height'], config['bytesperline'], config['sizeimage']
        needed = (h-1)*stride+w*2
        libc = ctypes.CDLL(None)
        compare = libc.memcmp; compare.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]; compare.restype = ctypes.c_int
        copy = libc.memmove; copy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]; copy.restype = ctypes.c_void_p
        previous_payload = ctypes.create_string_buffer(w*h*2)
        have_previous_payload = False
        req = bytearray(20); struct.pack_into('<3I', req, 0, 4, 1, 1); io(fd, 8, 20, req); allocated = True
        count = uint(req, 0)
        if not 1 <= count <= 32:
            raise RuntimeError('BAD_BUFFER_COUNT')
        for index in range(count):
            b = vbuffer(index); io(fd, 9, 88, b)
            length, offset = uint(b, 72), uint(b, 64)
            if length < size:
                raise RuntimeError('SHORT_MMAP_BUFFER')
            m = mmap.mmap(fd, length, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ|mmap.PROT_WRITE, offset=offset)
            maps.append(m); anchors.append(ctypes.c_ubyte.from_buffer(m))
            addresses.append(ctypes.addressof(anchors[-1])); io(fd, 15, 88, b)
        io(fd, 18, 4, bytearray(struct.pack('<I', 1)), 1); streaming = True
        absolute_deadline = time.monotonic()+DURATION_S+FRAME_TIMEOUT_S+2
        while True:
            loop_start = time.monotonic()
            kernel.poll()
            if kernel.storage_errors:
                raise RuntimeError('NEW_STORAGE_ERROR_STOP')
            if time.monotonic() >= absolute_deadline:
                raise RuntimeError('ABSOLUTE_CAPTURE_DEADLINE')
            deadline = min(absolute_deadline, time.monotonic()+FRAME_TIMEOUT_S)
            while True:
                if not select.select([fd], [], [], max(0, deadline-time.monotonic()))[0]:
                    raise RuntimeError('FRAME_TIMEOUT')
                b = vbuffer()
                try:
                    io(fd, 17, 88, b)
                    break
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EINTR) and time.monotonic() < deadline:
                        continue
                    raise
            arrival = time.monotonic()
            if arrival >= absolute_deadline:
                raise RuntimeError('ABSOLUTE_CAPTURE_DEADLINE')
            index, used, flags, sequence = uint(b, 0), uint(b, 8), uint(b, 12), uint(b, 56)
            sec, usec = struct.unpack_from('<qq', b, 24); timestamp_us = sec*1000000+usec
            if index >= len(maps):
                raise RuntimeError('BAD_DEQUEUED_INDEX')
            row = dict(sequence=sequence, v4l2_timestamp_us=timestamp_us, host_arrival_monotonic_s=arrival,
                       flags=flags, timestamp_type=hex(flags & 0xe000), timestamp_source=hex(flags & 0x70000),
                       bytesused=used, source_sequence_gap=0, duplicate_sequence=False,
                       backwards_sequence=False, repeated_timestamp=False, backwards_timestamp=False,
                       arrival_gap_ms=None, v4l2_timestamp_gap_ms=None)
            if flags & 0x40 or not needed <= used <= len(maps[index]):
                row.update(valid_payload=False, metric_scope='EXCLUDED_INVALID_PAYLOAD',
                           rejection_phase='startup' if first_valid is None else 'capture',
                           rejection_reason='BUFFER_FLAG_ERROR' if flags & 0x40 else 'INVALID_BYTESUSED')
                rows.append(row)
                if first_valid is None:
                    startup_rejections.append(row)
                else:
                    rejected.append(sequence)
                io(fd, 15, 88, b)
                if first_valid is not None and arrival-first_valid >= DURATION_S+FRAME_TIMEOUT_S:
                    raise RuntimeError('NO_VALID_END_FRAME')
                continue
            if timestamp_us < 0 or not 0 <= usec < 1000000:
                raise RuntimeError('INVALID_V4L2_TIMESTAMP')
            row.update(metric_scope='VALID_FRAME_SEQUENCE',
                       source_sequence_gap=max(0, sequence-previous[0]-1) if previous else 0,
                       duplicate_sequence=bool(previous and sequence == previous[0]),
                       backwards_sequence=bool(previous and sequence < previous[0]),
                       repeated_timestamp=bool(previous and timestamp_us == previous[1]),
                       backwards_timestamp=bool(previous and timestamp_us < previous[1]),
                       arrival_gap_ms=(arrival-previous[2])*1000 if previous else None,
                       v4l2_timestamp_gap_ms=(timestamp_us-previous[1])/1000 if previous else None)
            previous = (sequence, timestamp_us, arrival)
            native_start = time.monotonic()
            if stats(addresses[index], w, h, stride, native_out, int(GRID_4X4)):
                raise RuntimeError('NATIVE_STATISTICS_FAILED')
            native_ms = (time.monotonic()-native_start)*1000
            compare_start = time.monotonic()
            identical = None
            if stride == w*2:
                if have_previous_payload:
                    identical = compare(addresses[index], previous_payload, w*h*2) == 0
                copy(previous_payload, addresses[index], w*h*2)
                have_previous_payload = True
            total, zero, saturated = int(native_out[0]), int(native_out[1]), int(native_out[2])
            if total != w*h or native_out[5] == 0 or native_out[7] == 0:
                raise RuntimeError('NATIVE_STATISTICS_SHAPE_MISMATCH')
            row.update(valid_payload=True, width=w, height=h, dtype='<u2', nonzero_pixels=total-zero,
                       nonzero_ratio=(total-zero)/total, invalid_zero_ratio=zero/total, raw_65535_ratio=saturated/total,
                       minimum_raw=int(native_out[3]), maximum_raw=int(native_out[4]),
                       left_outer_nonzero_ratio=native_out[6]/native_out[5],
                       central_left_lower_nonzero_ratio=native_out[8]/native_out[7], native_stats_ms=native_ms)
            row['identical_payload_to_previous'] = identical
            row['content_compare_copy_ms'] = (time.monotonic()-compare_start)*1000
            if GRID_4X4:
                row['grid_4x4_nonzero_ratio'] = [native_out[9+i]/native_out[25+i] for i in range(16)]
            if first_valid is None:
                first_valid = arrival
            last_valid = arrival; elapsed = arrival-first_valid
            tag = 'first' if 'first' not in samples else 'middle' if elapsed >= DURATION_S/2 and 'middle' not in samples else None
            if elapsed >= DURATION_S:
                tag = 'last'
            if tag:
                raw = maps[index][:w*h*2] if stride == w*2 else b''.join(maps[index][y*stride:y*stride+w*2] for y in range(h))
                samples[tag] = (raw, row)
            row['processing_ms'] = (time.monotonic()-arrival)*1000
            row['iteration_ms'] = (time.monotonic()-loop_start)*1000
            rows.append(row)
            if elapsed >= DURATION_S:
                break
            io(fd, 15, 88, b)
    except Exception as exc:
        errors.append(repr(exc))
    finally:
        if fd is not None:
            if streaming:
                try:
                    io(fd, 19, 4, bytearray(struct.pack('<I', 1)), 1)
                except OSError as exc:
                    cleanup.append('STREAMOFF: '+repr(exc))
            anchors.clear(); addresses.clear()
            for m in maps:
                try:
                    m.close()
                except Exception as exc:
                    cleanup.append('MMAP_CLOSE: '+repr(exc))
            if allocated:
                try:
                    req = bytearray(20); struct.pack_into('<3I', req, 0, 0, 1, 1); io(fd, 8, 20, req)
                except OSError as exc:
                    cleanup.append('REQBUFS_FREE: '+repr(exc))
            try:
                os.close(fd)
            except OSError as exc:
                cleanup.append('DEVICE_CLOSE: '+repr(exc))
        if native_fd is not None:
            os.close(native_fd)
        if kernel:
            kernel.poll(force=True)
    good = [r for r in rows if r.get('valid_payload')]
    elapsed = last_valid-first_valid if first_valid is not None and last_valid is not None else 0.0
    abnormal = {key: sum(r[key] for r in good) for key in
                ('duplicate_sequence', 'backwards_sequence', 'repeated_timestamp', 'backwards_timestamp')}
    coverage_ok = len(good) >= 2 and elapsed >= DURATION_S and any(r['nonzero_pixels'] > 0 for r in good)
    gate_blockers = []
    if not coverage_ok: gate_blockers.append('INCOMPLETE_VALID_NONZERO_CAPTURE_WINDOW')
    if errors: gate_blockers.append('CAPTURE_EXCEPTION')
    if cleanup: gate_blockers.append('CLEANUP_ERROR')
    if any(abnormal.values()): gate_blockers.append('SEQUENCE_OR_TIMESTAMP_REPEAT_OR_BACKWARDS')
    if rejected: gate_blockers.append('REJECTED_PAYLOAD_OR_BUFFER_ERROR')
    if sum(r['source_sequence_gap'] for r in good): gate_blockers.append('OBSERVED_SEQUENCE_GAP')
    if len(samples) != 3: gate_blockers.append('THREE_SAMPLES_NOT_CAPTURED')
    if not kernel or kernel.status != 'READABLE' or kernel.failures: gate_blockers.append('KERNEL_LOG_UNKNOWN')
    if kernel and (kernel.storage_errors or kernel.baseline_storage_errors): gate_blockers.append('STORAGE_ERROR')
    if kernel and kernel.usb_events: gate_blockers.append('NEW_USB_EVENT')
    passed = not gate_blockers
    summary = dict(status='PASS' if passed else 'PARTIAL' if coverage_ok else 'FAIL', provenance='ACTUAL_KV260_NATIVE_V4L2_D455_CONTINUOUS',
                   requested_seconds=DURATION_S, captured_window_seconds=elapsed, total_wall_seconds=time.monotonic()-started,
                   valid_frames=len(good), dequeued_frames=len(rows), rejected_sequences=rejected,
                   measurement_version=2, metric_scope='VALID_PAYLOAD_FRAMES_ONLY',
                   startup_rejection_count=len(startup_rejections), startup_rejections=startup_rejections,
                   actual_host_fps=(len(good)-1)/elapsed if elapsed > 0 else None,
                   first_sequence=good[0]['sequence'] if good else None, last_sequence=good[-1]['sequence'] if good else None,
                   source_sequence_gaps=sum(r['source_sequence_gap'] for r in good), **abnormal,
                   arrival_interval_ms=quantiles([r['arrival_gap_ms'] for r in good if r['arrival_gap_ms'] is not None]),
                   v4l2_timestamp_interval_ms=quantiles([r['v4l2_timestamp_gap_ms'] for r in good if r['v4l2_timestamp_gap_ms'] is not None]),
                   native_stats_ms=quantiles([r['native_stats_ms'] for r in good]),
                   content_compare_copy_ms=quantiles([r['content_compare_copy_ms'] for r in good]),
                   exact_consecutive_identical_payload_count=sum(r.get('identical_payload_to_previous') is True for r in good),
                   processing_ms=quantiles([r['processing_ms'] for r in good]),
                   nonzero_ratio=ratios([r['nonzero_ratio'] for r in good]),
                   invalid_zero_ratio=ratios([r['invalid_zero_ratio'] for r in good]),
                   raw_65535_ratio=ratios([r['raw_65535_ratio'] for r in good]),
                   roi_nonzero_ratio={key: ratios([r[key+'_nonzero_ratio'] for r in good])
                                      for key in ('left_outer', 'central_left_lower')},
                   depth_scale_m=None, depth_scale_status='UNKNOWN_NOT_READ_FROM_DEVICE',
                   native_statistics_sha256=NATIVE_SHA256, config=config, identity=identity,
                   errors=errors, gate_blockers=gate_blockers,
                   first_decisive_error=errors[0] if errors else gate_blockers[0] if gate_blockers else None,
                   cleanup_errors=cleanup,
                   content_duplicate_detection=('EXACT_NATIVE_MEMCMP_ON_CONSECUTIVE_VALID_PACKED_Z16; same scene is not a fault'
                       if config.get('bytesperline') == WIDTH*2 else 'NOT_MEASURED_PADDED_STRIDE'),
                   samples=list(samples), storage_status=('UNKNOWN' if not kernel or kernel.status != 'READABLE'
                     else 'ERROR' if kernel.storage_errors or kernel.baseline_storage_errors else 'NO_NEW_ERRORS'),
                   exported_raw_samples=['last'] if 'last' in samples else [],
                   hash_only_samples=[tag for tag in ('first', 'middle') if tag in samples],
                   note='Startup rejected buffers remain explicitly recorded and are not valid frames or part of the timed window. '
                        'Any rejected payload inside the timed capture window blocks PASS. '
                        'Sequence gaps are receive observations, not proven USB loss. No FPS or validity threshold. '
                        'V4L2 kernel timestamps are not a calibrated physical-exposure latency measurement. '
                        'Statistics run natively on mmap, per-frame hashes are not computed.')
    print('V4L2_CONT_META '+json.dumps(summary, allow_nan=False), flush=True)
    print('V4L2_CONT_KERNEL '+json.dumps(kernel.report() if kernel else {'status':'UNKNOWN'}, allow_nan=False), flush=True)
    export_frames(rows)
    export_samples(samples)
    print('V4L2_CONT_END '+summary['status'], flush=True)
    return 0 if passed else 3

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('V4L2_CONT_FATAL '+repr(exc), flush=True)
        raise SystemExit(3)
