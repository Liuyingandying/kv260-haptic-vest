"""Offline unified candidate entry; deliberately has no camera/UIO/serial backend.

Saved BDP1 is processed by the copied, unchanged C++ core in --stream mode.
Recorded FRESH bits describe ONLY recorded clocks; they never authorize live
control. KPF1 bytes and immutable core predictions are kept separate from the
current-run decision, which always forbids physical output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time

import kpf1_codec as codec
import quality_bridge

ROOT = Path(__file__).resolve().parents[1]
BDP_HEADER = struct.Struct('<4s5I3Qd2I')
SCHEMA = 'kv260_unified_offline_v1'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def recorded_frames(data, source):
    """Validate framing without changing source values or inventing clocks."""
    offset = 0
    records = []
    while offset < len(data):
        if len(data) - offset < BDP_HEADER.size:
            raise ValueError('TRUNCATED_BDP1_HEADER')
        fields = BDP_HEADER.unpack_from(data, offset)
        magic, w, h, stride, kind, session, sequence, capture, processing, scale, coord, reserved = fields
        if magic != b'BDP1' or kind not in (1, 2) or coord not in (1, 2) or reserved:
            raise ValueError('INVALID_BDP1_HEADER')
        if not session or (not sequence and (w or h)):
            raise ValueError('INVALID_BDP1_IDENTITY')
        if not math.isfinite(scale) or scale <= 0 or (kind == 1 and scale != 1.):
            raise ValueError('INVALID_BDP1_SCALE')
        gap = w == h == stride == 0
        if not gap and not (16 <= w <= 4096 and 16 <= h <= 4096 and
                            w * (4 if kind == 1 else 2) <= stride <= 65536):
            raise ValueError('INVALID_BDP1_DIMENSIONS_OR_STRIDE')
        length = 64 + (0 if gap else stride * h)
        if offset + length > len(data):
            raise ValueError('TRUNCATED_BDP1_PAYLOAD')
        raw = data[offset:offset + length]
        records.append(dict(source_file=str(source), source_file_sha256=sha256(data),
                            record_offset=offset, record_sha256=sha256(raw), raw=raw,
                            width=w, height=h, stride=stride, kind=kind, session=session,
                            source_sequence=sequence, capture_ns=capture,
                            processing_ns=processing, scale=scale, coordinate=coord,
                            gap=gap, timestamp_present=bool(capture and processing)))
        offset += length
    if not records:
        raise ValueError('EMPTY_BDP1_INPUT')
    return records


def check_config(path):
    values = {}
    for line in Path(path).read_text(encoding='ascii').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if key in values:
            raise ValueError('DUPLICATE_CONFIG_KEY')
        values[key] = float(value)
    if values != quality_bridge.CONFIG:
        raise ValueError('FROZEN_CONFIGURATION_MISMATCH')


def run_core(records, executable, config, timeout=60.):
    check_config(config)
    if not Path(executable).is_file():
        raise ValueError('HOST_CORE_EXECUTABLE_MISSING')
    began = time.perf_counter_ns()
    proc = subprocess.run([str(Path(executable).resolve()), '--stream', '--config',
                           str(Path(config).resolve()), '--timing'],
                          input=b''.join(r['raw'] for r in records),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, check=False)
    elapsed = time.perf_counter_ns() - began
    if proc.returncode:
        raise ValueError('CORE_EXIT_NONZERO:' + str(proc.returncode) + ':' +
                         proc.stderr.decode('utf-8', errors='replace')[-2000:])
    outputs = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    if len(outputs) != len(records):
        raise ValueError('CORE_RESULT_COUNT_MISMATCH')
    for record, result in zip(records, outputs):
        if any(result.get(k) != v for k, v in (
                ('session', record['session']),
                ('seq', 0 if record['gap'] else record['source_sequence']),
                ('width', record['width']), ('height', record['height']),
                ('processing_ns', record['processing_ns']),
                ('capture_ns', record['processing_ns'] if record['gap'] else record['capture_ns']))):
            raise ValueError('CORE_FRAME_IDENTITY_MISMATCH')
    return outputs, dict(exit_code=proc.returncode, host_process_wall_ns=elapsed,
                         timing_scope='Host process startup + decode + core + JSON + pipe; not KV260 benchmark',
                         executable_sha256=sha256(Path(executable).read_bytes()),
                         config_sha256=sha256(Path(config).read_bytes()),
                         stderr=proc.stderr.decode('utf-8', errors='replace'))


def project_record(record, core, event_sequence):
    if record['gap']:
        packet = codec.Packet(record['session'], event_sequence, record['processing_ns'], 0,
                              codec.Quality.UNUSABLE, codec.GAP, 1, codec.Side(), codec.Side(),
                              record['coordinate'], 0, 0, bytes(256), b'\xff' * 32).validate()
        return packet, None
    # These are the ORIGINAL stored clock values, not time.monotonic_ns() now.
    fault = None if record['timestamp_present'] else 'MISSING_RECORDED_TIMESTAMP'
    envelope = quality_bridge.annotate_cpp(core, now_ns=record['processing_ns'], source_fault=fault)
    packet, encoded = quality_bridge.form_kpf1_projection(
        envelope, codec, record['processing_ns'], event_sequence)
    if encoded != codec.decode(encoded).encode():
        raise ValueError('KPF1_ROUNDTRIP_MISMATCH')
    return packet, envelope


def packet_summary(packet):
    """Recorded protocol eligibility and present authorization are different."""
    packet.validate()
    return dict(quality=codec.Quality(packet.quality).name,
                session=packet.session, sequence=packet.sequence,
                recorded_capture_ns=packet.capture_ns, recorded_age_us=packet.age_us,
                recorded_fresh_flag=bool(packet.flags & codec.FRESH),
                recorded_control_candidate=bool(packet.control_candidate),
                current_live_freshness='NOT_VERIFIED_OFFLINE',
                current_control_eligible=False, physical_output_permitted=False,
                unknown_cells=sum(v.bit_count() for v in packet.unknown),
                nonzero_cells=sum(bool(v) for v in packet.matrix),
                matrix=list(packet.matrix), unknown_lsb_first_hex=packet.unknown.hex(),
                left=dict(kind=int(packet.left.kind), distance_mm=packet.left.distance_mm,
                          flags=packet.left.flags),
                right=dict(kind=int(packet.right.kind), distance_mm=packet.right.distance_mm,
                           flags=packet.right.flags),
                inhibition_reasons=['OFFLINE_INPUT_NOT_LIVE', 'NO_HARDWARE_OUTPUT_BACKEND'])


def run_offline(inputs, output_dir, *, executable=None, config=None, timeout=60.):
    paths = [Path(p).resolve() for p in inputs]
    if not paths or any(not p.is_file() for p in paths):
        raise ValueError('INPUT_MUST_BE_EXISTING_REGULAR_FILES')
    suffixes = {p.suffix.lower() for p in paths}
    bdp_mode = suffixes <= {'.bdp', '.bdp1'}
    kpf_mode = suffixes <= {'.kpf', '.kpf1', '.bin'}
    if not bdp_mode and not kpf_mode:
        raise ValueError('USE_ONLY_BDP1_OR_ONLY_KPF1_INPUTS')
    out = Path(output_dir).resolve()
    if out.exists():
        raise ValueError('OUTPUT_DIRECTORY_ALREADY_EXISTS_NO_OVERWRITE')
    frames = []
    receipt = None
    if bdp_mode:
        if executable is None or config is None:
            raise ValueError('BDP1_REQUIRES_HOST_CORE_AND_FROZEN_CONFIG')
        records = []
        for path in paths:
            records.extend(recorded_frames(path.read_bytes(), path))
        outputs, receipt = run_core(records, executable, config, timeout)
        for index, (record, core) in enumerate(zip(records, outputs), 1):
            packet, envelope = project_record(record, core, index)
            metadata = {k: v for k, v in record.items() if k != 'raw'}
            frames.append((packet, dict(source=metadata, core_result=core,
                                        quality_envelope=envelope)))
    else:
        seen = set()
        max_sequence = {}
        for path in paths:
            data = path.read_bytes()
            packet = codec.decode(data)
            identity = (packet.session, packet.sequence)
            temporal = ('DUPLICATE_PACKET' if identity in seen else
                        'OUT_OF_ORDER_PACKET' if packet.sequence <= max_sequence.get(packet.session, 0)
                        else 'RECORDED_PACKET_ONLY')
            seen.add(identity)
            max_sequence[packet.session] = max(packet.sequence, max_sequence.get(packet.session, 0))
            frames.append((packet, dict(source=dict(source_file=str(path), source_file_sha256=sha256(data)),
                                        temporal_audit=temporal, core_result=None, quality_envelope=None)))
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    for index, (packet, detail) in enumerate(frames, 1):
        stem = 'frame_' + str(index).zfill(4)
        encoded = packet.encode()
        if codec.decode(encoded) != packet:
            raise ValueError('KPF1_FIELD_ROUNDTRIP_MISMATCH')
        words = struct.unpack('<96I', encoded)
        files = {
            stem + '.kpf': encoded,
            stem + '.mem': ('// OFFLINE_REPLAY_ONLY: no live/control authorization\n' +
                            ''.join(f'{word:08x}\n' for word in words)).encode('ascii'),
            stem + '.vh': ('// OFFLINE_REPLAY_ONLY; include inside initial; reg [31:0] kpf_words [0:95];\n' +
                           ''.join(f"kpf_words[{i}] = 32'h{word:08x};\n" for i, word in enumerate(words))).encode('ascii')}
        summary = packet_summary(packet)
        summary.update(index=index, kpf1_sha256=sha256(encoded), bytes=len(encoded), words=len(words))
        detail.update(schema=SCHEMA, mode='OFFLINE_SAVED_INPUT', decision=summary,
                      hardware_access_performed=False)
        files[stem + '.json'] = (json.dumps(detail, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        for name, content in files.items():
            with (out / name).open('xb') as stream:
                stream.write(content)
        rows.append({k: v for k, v in summary.items() if k not in ('matrix', 'left', 'right')})
    report = dict(schema=SCHEMA, mode='OFFLINE_SAVED_INPUT', frames=len(frames),
                  hardware_access_performed=False, physical_output_permitted=False,
                  live_freshness='NOT_VERIFIED', board_runtime='NOT_VERIFIED',
                  historical_clock_scope='Only stored BDP1/KPF1 timestamps; no exposure/clock mapping proof',
                  frozen_bridge_sha256=sha256(Path(quality_bridge.__file__).read_bytes()),
                  frozen_codec_sha256=sha256(Path(codec.__file__).read_bytes()),
                  core_execution=receipt, results=rows)
    with (out / 'OFFLINE_RESULT.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('offline', 'live'), default='offline')
    parser.add_argument('--input', nargs='+', required=True, help='Saved .bdp/.bdp1 or .kpf/.kpf1/.bin files')
    parser.add_argument('--core-executable', type=Path)
    parser.add_argument('--config', type=Path, default=ROOT / 'core' / 'core_config.txt')
    parser.add_argument('--output-dir', type=Path, required=True, help='Must not already exist')
    parser.add_argument('--timeout-s', type=float, default=60.)
    args = parser.parse_args(argv)
    if args.mode != 'offline':
        parser.error('LIVE_BACKEND_NOT_IMPLEMENTED_OR_AUTHORIZED: do not deploy the old IIC-disabled live bridge')
    if not math.isfinite(args.timeout_s) or not 0 < args.timeout_s <= 600:
        parser.error('offline host process timeout must be in (0, 600] seconds')
    try:
        result = run_offline(args.input, args.output_dir, executable=args.core_executable,
                             config=args.config, timeout=args.timeout_s)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps(dict(schema=SCHEMA, status='FAIL', error=str(exc),
                              hardware_access_performed=False, physical_output_permitted=False)), file=sys.stderr)
        return 2
    print(json.dumps(dict(status='PASS_OFFLINE_ONLY', frames=result['frames'],
                          output_dir=str(args.output_dir.resolve()),
                          physical_output_permitted=False, hardware_access_performed=False)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
