"""Linux atomic native-frame IPC. Importing this module opens no device or socket.

This source never starts the SDK.  A caller must separately authorize and launch
the independent native publisher. Receipt time is not capture/exposure time.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import stat
import struct
import threading
import time
from typing import Any
from native_json_parser import loads_native, parser_info


class SourceClosed(EOFError):
    pass


class SourceProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ReceivedNative:
    native: dict
    receipt_monotonic_ns: int
    source_origin: str = "UNIX_SEQPACKET_METADATA_NOT_CAPTURE_TIME"
    decode_end_monotonic_ns: int | None = None


class LatestSlot:
    """One pending object; replacing it never grows a FIFO."""
    def __init__(self):
        self._condition = threading.Condition()
        self._latest = None
        self.closed = False
        self.offered = self.replaced = self.popped = 0

    def offer(self, value: Any) -> None:
        with self._condition:
            if self.closed:
                raise SourceClosed("LATEST_SLOT_CLOSED")
            self.offered += 1
            if self._latest is not None:
                self.replaced += 1
            self._latest = value
            self._condition.notify_all()

    def pop(self, timeout_s: float = 1.0):
        if timeout_s < 0:
            raise ValueError("NEGATIVE_TIMEOUT")
        end = time.monotonic() + timeout_s
        with self._condition:
            while self._latest is None and not self.closed:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("NO_NEW_NATIVE_FRAME")
                self._condition.wait(remaining)
            if self.closed:
                raise SourceClosed("LATEST_SLOT_CLOSED")
            value, self._latest = self._latest, None
            self.popped += 1
            return value

    def close(self):
        with self._condition:
            self.closed = True
            # A dead producer must not leave a formerly queued frame available.
            self._latest = None
            self._condition.notify_all()


class SeqpacketFrameSource:
    """One Linux UDS client, atomic JSON records, bounded kernel + latest queues.

    Only same-euid clients are accepted (SO_PEERCRED). A Linux host is required;
    Windows/FakeSocket tests must not be reported as actual UDS/camera tests.
    """
    def __init__(self, socket_path, max_frame_bytes=1_048_576,
                 socket_buffer_bytes=2_097_152, *, clock_ns=time.monotonic_ns):
        self.path = Path(socket_path)
        if not 1024 <= max_frame_bytes <= 1_048_576:
            raise ValueError("FRAME_BOUND_OUT_OF_RANGE")
        if not max_frame_bytes <= socket_buffer_bytes <= 4_194_304:
            raise ValueError("SOCKET_BUFFER_BOUND_OUT_OF_RANGE")
        self.max_frame_bytes, self.socket_buffer_bytes = max_frame_bytes, socket_buffer_bytes
        self.clock_ns = clock_ns
        self.slot = LatestSlot()
        self.events = deque(maxlen=64)
        self.stats = dict(messages=0, bytes=0, rejected_duplicate=0,
                          rejected_out_of_order=0, protocol_errors=0,
                          sequence_gaps=0, sdk_index_gaps=0,
                          kernel_receive_buffer_bytes=None,
                          JSON_parser=parser_info(),JSON_parse_ns_total=0,
                          JSON_parse_ns_max=0)
        self._listener = self._connection = self._thread = None
        self._stop = threading.Event()
        self._owned_inode = None
        self._last_session = self._last_sequence = self._last_sdk_index = None
        self.error = None

    def _event(self, stage, result, error=None):
        self.events.append(dict(monotonic_ns=self.clock_ns(), stage=stage,
                                result=result, error=error))

    def start(self):
        if self._thread is not None or self.slot.closed:
            raise RuntimeError("SOURCE_ALREADY_STARTED_OR_CLOSED")
        if self.path.exists() or self.path.is_symlink():
            raise FileExistsError("SOCKET_PATH_EXISTS_NOT_OVERWRITTEN")
        if os.name != "posix" or not all(hasattr(socket, k) for k in ("AF_UNIX", "SOCK_SEQPACKET", "SO_PEERCRED")):
            raise NotImplementedError("LINUX_UNIX_SEQPACKET_REQUIRED")
        if len(os.fsencode(self.path)) > 100:
            raise ValueError("SOCKET_PATH_TOO_LONG")
        parent = self.path.parent.stat()
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o077:
            raise PermissionError("PRIVATE_OWNED_0700_SOCKET_DIRECTORY_REQUIRED")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.socket_buffer_bytes)
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)  # New private IPC socket only, never USB/device permissions.
            self._owned_inode = self.path.stat().st_ino
            listener.listen(1)
            listener.settimeout(0.2)
            self._listener = listener
            self._thread = threading.Thread(target=self._accept_then_receive,
                                            name="gemini-r4-seqpacket", daemon=True)
            self._thread.start()
            self._event("IPC_LISTEN", "READY_NO_SDK_STARTED")
            return self
        except BaseException:
            listener.close()
            self._unlink_owned_socket()
            raise

    def _accept_then_receive(self):
        try:
            while not self._stop.is_set():
                try:
                    connection, _ = self._listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            self._connection = connection
            _pid, uid, _gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                raise PermissionError("IPC_PEER_UID_MISMATCH")
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.socket_buffer_bytes)
            connection.settimeout(0.2)
            self.stats["kernel_receive_buffer_bytes"] = connection.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            self._event("IPC_ACCEPT", "SAME_UID_PEER_ONLY_NOT_CAMERA_AUTHENTICATION")
            self._receive(connection)
        except BaseException as exc:
            if not self._stop.is_set():
                self.error = f"{type(exc).__name__}:{exc}"
                self._event("IPC_RECEIVE", "FAIL", self.error)
        finally:
            self.slot.close()
            if self._connection is not None:
                self._connection.close()

    def _ingest(self, data: bytes, flags=0, *, source_origin="UNIX_SEQPACKET_METADATA_NOT_CAPTURE_TIME"):
        """Pure record validation, also exercised with deterministic fake transport."""
        if flags & getattr(socket, "MSG_TRUNC", 32) or len(data) > self.max_frame_bytes:
            self.stats["protocol_errors"] += 1
            raise SourceProtocolError("ATOMIC_FRAME_TRUNCATED_OR_TOO_LARGE")
        if not data:
            raise SourceClosed("IPC_PEER_EOF")
        receipt = self.clock_ns()
        try:
            native = loads_native(data)
        except (UnicodeError, ValueError) as exc:
            self.stats["protocol_errors"] += 1
            raise SourceProtocolError("FRAME_NOT_SINGLE_UTF8_JSON_OBJECT") from exc
        decoded = self.clock_ns()
        if not isinstance(native, dict) or native.get("schema") != "GEMINI_NATIVE_DIAGNOSTIC_V1":
            raise SourceProtocolError("FULL_NATIVE_SCHEMA_REQUIRED")
        if native.get("mode") != "REAL_SDK_DEPTH_OBSERVATION":
            raise SourceProtocolError("SDK_NATIVE_MODE_REQUIRED_NOT_DISPLAY_OR_REPLAY")
        session, sequence, sdk_index = (native.get(k) for k in ("session", "sequence", "sdk_frame_index"))
        if not all(type(x) is int for x in (session, sequence, sdk_index)) or not 0 < session <= 0xffffffff or sequence <= 0 or sdk_index < 0:
            raise SourceProtocolError("NATIVE_SEQUENCE_FIELDS_INVALID")
        if self._last_session is not None and session != self._last_session:
            raise SourceProtocolError("CAPTURE_SESSION_CHANGED_NEW_SOURCE_REQUIRED")
        if self._last_sequence is not None:
            if sequence == self._last_sequence or sdk_index == self._last_sdk_index:
                self.stats["rejected_duplicate"] += 1
                self._event("FRAME_ORDER", "REJECT_DUPLICATE")
                return False
            if sequence < self._last_sequence or sdk_index < self._last_sdk_index:
                self.stats["rejected_out_of_order"] += 1
                self._event("FRAME_ORDER", "REJECT_OUT_OF_ORDER")
                return False
            self.stats["sequence_gaps"] += max(0, sequence - self._last_sequence - 1)
            self.stats["sdk_index_gaps"] += max(0, sdk_index - self._last_sdk_index - 1)
        # Receiver time is separate metadata. No SDK offset/clock guessing here.
        self.stats['JSON_parse_ns_total'] += decoded-receipt
        self.stats['JSON_parse_ns_max'] = max(self.stats['JSON_parse_ns_max'],decoded-receipt)
        value = ReceivedNative(native, receipt, source_origin, decoded)
        self.slot.offer(value)
        self._last_session, self._last_sequence, self._last_sdk_index = session, sequence, sdk_index
        self.stats["messages"] += 1
        self.stats["bytes"] += len(data)
        return True

    def _receive(self, connection):
        while not self._stop.is_set():
            try:
                data, _ancillary, flags, _address = connection.recvmsg(self.max_frame_bytes)
            except socket.timeout:
                continue
            self._ingest(data, flags)

    def next_frame(self, timeout_s=1.0):
        try:
            return self.slot.pop(timeout_s)
        except SourceClosed as exc:
            raise SourceClosed(self.error or "IPC_SOURCE_CLOSED") from exc

    def _unlink_owned_socket(self):
        if self._owned_inode is not None:
            try:
                st = self.path.lstat()
                if stat.S_ISSOCK(st.st_mode) and st.st_ino == self._owned_inode:
                    self.path.unlink()
            except FileNotFoundError:
                pass

    def close(self):
        self._stop.set()
        self.slot.close()
        for connection in (self._connection, self._listener):
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                raise RuntimeError("IPC_THREAD_DID_NOT_STOP_WITHIN_1S")
        self._unlink_owned_socket()
        self._event("IPC_CLOSE", "COMPLETED_NO_SDK_OPERATION")
