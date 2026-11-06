"""The present FCH1 image's unchanged KPF1 contract, not a new wire protocol.

Only standard-library byte/file code is loaded. This module cannot open MMIO,
SDK, camera or serial interfaces. See docs/PROTOCOL_CONTRACT.md for the current
BD/RTL proof for these addresses and for the absence of a wire CRC field.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_CODEC_PATH = (Path(__file__).resolve().parents[1] / "vendor" /
               "CODE_ARCHITECTURE_REVIEW/research/optimization_v2/frozen_v1/source/"
               "kv260/linux/runtime/kpf1_codec.py")
_NAME = "_r4_frozen_kpf1_codec"
if _NAME not in sys.modules:
    _SPEC = importlib.util.spec_from_file_location(_NAME, _CODEC_PATH)
    if _SPEC is None or _SPEC.loader is None:
        raise ImportError("FROZEN_CODEC_NOT_LOADABLE")
    _MODULE = importlib.util.module_from_spec(_SPEC)
    sys.modules[_NAME] = _MODULE
    _OLD_DONT_WRITE = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        _SPEC.loader.exec_module(_MODULE)
    finally:
        sys.dont_write_bytecode = _OLD_DONT_WRITE
_codec = sys.modules[_NAME]

Packet, Quality, Side, RiskKind = (_codec.Packet, _codec.Quality, _codec.Side,
                                  _codec.RiskKind)
FRESH, GAP, RESUME = _codec.FRESH, _codec.GAP, _codec.RESUME
SUPPORTED, UNKNOWN, COVERAGE_COMPLETE = (1, 2, 4)
pack_unknown, unpack_unknown = _codec.pack_unknown, _codec.unpack_unknown
MAGIC, VERSION = _codec.MAGIC, _codec.VERSION
PACKET_BYTES = TOTAL_BYTES = _codec.TOTAL_BYTES
HEADER_BYTES, MATRIX_BYTES, UNKNOWN_BYTES = (96, 256, 32)
WORD_COUNT, WORD_BYTES = 96, 4
COVERAGE_PPM, MAX_AGE_US, FUTURE_US = (900000, 250000, 40000)
NO_DISTANCE = 0xFFFFFFFF

# FCH1 is a diagnostic/bitstream identity. The frame magic remains KPF1.
FCH1_IMAGE_ID = EXPECTED_IDENTITY = 0x46434831
MAILBOX_BASE, STATUS_BASE, GPIO_BASE = (0x9F000000, 0x9C000000, 0x9E000000)
WINDOW_SIZE = 0x10000
COMMAND, STATUS, GENERATION, REASON = (0x000, 0x004, 0x008, 0x00C)
SESSION, SEQUENCE_LOW, SEQUENCE_HIGH = (0x010, 0x014, 0x018)
CURRENT_AGE, LEGACY_GENERATION = (0x020, 0x028)
STAGING, ACTIVE = (0x100, 0x400)
BEGIN, SEAL, ABORT = (1, 2, 3)
REG_COMMAND, REG_STATUS, REG_GENERATION, REG_REASON = (COMMAND, STATUS,
                                                       GENERATION, REASON)
RX_OPEN = 1 << 0
ACTIVE_VALID = 1 << 1
CONTROL_VALID = 1 << 2
FORCE_OFF = 1 << 3
BUSY = 1 << 4
RESUME_REQUIRED = 1 << 5
NEW_SESSION_REQUIRED = 1 << 6
PROTOCOL_FAULT = 1 << 7
CRC_AVAILABLE = False


class UnsupportedProtocolFeature(ValueError):
    """A requested capability is absent from the frozen current RTL."""


def require_crc() -> None:
    """Do not silently invent or omit a CRC when the caller requires one."""
    raise UnsupportedProtocolFeature("CURRENT_RTL_HAS_NO_CRC_FIELD")


def encode_packet(packet: Packet) -> bytes:
    """Encode the existing 384-byte little-endian layout without new fields."""
    if not isinstance(packet, Packet):
        raise TypeError("CURRENT_PACKET_OBJECT_REQUIRED")
    data = packet.encode()
    if len(data) != PACKET_BYTES:
        raise ValueError("CURRENT_PACKET_LENGTH")
    return data


def decode_packet(data: bytes) -> Packet:
    if type(data) is not bytes:
        raise TypeError("IMMUTABLE_PACKET_BYTES_REQUIRED")
    packet = _codec.decode(data)
    if packet.encode() != data:
        raise ValueError("NON_CANONICAL_PACKET")
    return packet


def matrix_sha256(matrix: bytes) -> str:
    import hashlib
    if type(matrix) is not bytes or len(matrix) != MATRIX_BYTES or any(v > 4 for v in matrix):
        raise ValueError("MATRIX_REQUIRES_256_LEVEL0_TO4_BYTES")
    return hashlib.sha256(matrix).hexdigest()


if (MAGIC, VERSION, PACKET_BYTES, _codec.COVERAGE_PPM, _codec.MAX_AGE_US) != (
        0x3146504B, 1, 384, 900000, 250000):
    raise RuntimeError("VENDORED_CURRENT_CONTRACT_MISMATCH")
