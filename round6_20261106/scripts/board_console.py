"""Bounded KV260 root-console stages. No login/password or automatic retries."""
from pathlib import Path
import base64
import os
import hashlib
import json
import re
import struct
import textwrap
import time
import uuid
import zlib
import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parents[1]


def plain(data):
    return re.sub(rb'\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]', b'', bytes(data)).replace(b'\r', b'')


class Console:
    def __init__(self, name):
        self.out = ROOT / 'evidence' / name
        self.out.mkdir(parents=True, exist_ok=False)
        self.rx = bytearray()
        self.raw = (self.out / 'console.raw.log').open('xb')
        selected_port = os.environ.get('KV260_UART_PORT')
        selected_serial = os.environ.get('KV260_UART_SERIAL')
        if not selected_port and not selected_serial:
            raise RuntimeError('SET_EXPLICIT_KV260_UART_PORT_OR_SERIAL')
        matches = [p for p in list_ports.comports() if p.vid == 0x0403 and p.pid == 0x6011
                   and (not selected_port or p.device.casefold() == selected_port.casefold())
                   and (not selected_serial or p.serial_number == selected_serial)]
        if len(matches) != 1:
            raise RuntimeError('CONSOLE_IDENTITY_NOT_UNIQUE')
        self.port = serial.Serial(port=None, baudrate=115200, timeout=.05, write_timeout=10,
                                  xonxoff=False, rtscts=False, dsrdtr=False)
        self.port.dtr = False
        self.port.rts = False
        self.port.port = matches[0].device
        try:
            self.port.open()
            self.port.write(b'\r')
            self.until(rb'(?m)^root@kria:[^\n]*# ?$', 3, 0)
        except BaseException:
            if self.port.is_open:
                self.port.close()
            self.raw.close()
            raise

    def listen(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            data = self.port.read(self.port.in_waiting or 1)
            if data:
                self.rx.extend(data)
                self.raw.write(data)
                self.raw.flush()

    def until(self, pattern, seconds, start):
        end = time.monotonic() + seconds
        reported = 0
        while time.monotonic() < end:
            self.listen(.05)
            # End markers are short. Avoid rescanning multi-megabyte returned
            # frame JSON on every serial chunk.
            match = re.search(pattern, plain(self.rx[max(start,len(self.rx)-8192):]))
            if match:
                return match
            if len(self.rx)-start-reported >= 131072:
                reported = len(self.rx)-start
                print('RECEIVE_PROGRESS bytes=' + str(reported), flush=True)
        raise TimeoutError('STAGE_MARKER_ABSENT_NO_AUTOMATIC_RETRY')

    def send_source(self, source, name):
        folder = self.out / name
        folder.mkdir(exist_ok=False)
        (folder / 'source.py').write_text(source, encoding='utf-8')
        token = 'FAST_END_' + uuid.uuid4().hex
        wrapped = 'import traceback\ntry:\n' + textwrap.indent(source, '    ')
        wrapped += "\nexcept BaseException as e:\n    traceback.print_exc()\n    print('FAST_STAGE_ERROR ' + repr(e), flush=True)\nfinally:\n    print(" + repr(token) + ', flush=True)\n'
        compile(wrapped, '<readonly_or_authorized_stage>', 'exec')
        blob = base64.b64encode(zlib.compress(wrapped.encode('utf-8'), 9)).decode('ascii')
        lines = ["python3 -B - <<'FAST_SOURCE_EOF'", 'import base64,zlib', 'blob=(']
        lines += [repr(blob[i:i+120]) for i in range(0, len(blob), 120)]
        lines += [')', 'exec(zlib.decompress(base64.b64decode(blob)))', 'FAST_SOURCE_EOF']
        start = len(self.rx)
        for line in lines:
            self.port.write(line.encode('ascii') + b'\r')
            self.listen(.004)
        return token, start, folder

    def run(self, source, name, timeout=30):
        token, start, folder = self.send_source(source, name)
        self.until(rb'(?m)^' + token.encode() + rb'\n', timeout, start)
        output = plain(self.rx[start:]).decode('utf-8', 'replace')
        (folder / 'console.txt').write_text(output, encoding='utf-8')
        if re.search(r'(?m)^FAST_STAGE_ERROR ', output):
            raise RuntimeError('BOARD_STAGE_FAILED_SEE_' + name)
        records = [json.loads(line[len('FAST_RESULT '):]) for line in output.splitlines() if line.startswith('FAST_RESULT ')]
        if len(records) != 1:
            raise RuntimeError('BOARD_RESULT_NOT_UNIQUE')
        (folder / 'result.json').write_text(json.dumps(records[0], ensure_ascii=False, indent=2), encoding='utf-8')
        return records[0]

    def upload(self, source, name, payload, nonce):
        token, start, folder = self.send_source(source, name)
        digest = hashlib.sha256(payload).hexdigest()
        self.until(rb'(?m)^RAM_READY_' + nonce.encode() + b':' + str(len(payload)).encode() + b':' + digest.encode() + rb'\n', 10, start)
        for seq, offset in enumerate(range(0, len(payload), 32768)):
            block = payload[offset:offset+32768]
            wire = struct.pack('<4sII', b'KVPK', seq, len(block)) + hashlib.sha256(block).digest() + block
            mark = len(self.rx)
            self.port.write(wire)
            self.until(rb'(?m)^RAM_ACK_' + nonce.encode() + b':' + str(seq).encode() + b':' + str(offset+len(block)).encode() + rb'\n', 18, mark)
            print('TRANSFER_PROGRESS ' + str(offset+len(block)) + '/' + str(len(payload)), flush=True)
        self.until(rb'(?m)^' + token.encode() + rb'\n', 25, start)
        output = plain(self.rx[start:]).decode('utf-8', 'replace')
        (folder / 'console.txt').write_text(output, encoding='utf-8')
        if re.search(r'(?m)^FAST_STAGE_ERROR ', output):
            raise RuntimeError('UPLOAD_BOARD_STAGE_FAILED')
        rows = [json.loads(line[len('FAST_RESULT '):]) for line in output.splitlines() if line.startswith('FAST_RESULT ')]
        if len(rows) != 1:
            raise RuntimeError('UPLOAD_NO_RESULT')
        (folder / 'result.json').write_text(json.dumps(rows[0], indent=2), encoding='utf-8')
        return rows[0]

    def close(self):
        if self.port.is_open:
            self.port.close()
        self.raw.close()
        (self.out / 'console.txt').write_text(plain(self.rx).decode('utf-8', 'replace'), encoding='utf-8')
