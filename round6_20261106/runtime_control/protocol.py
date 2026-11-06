"""Current KPF1 ABI reused verbatim from the existing frozen codec wrapper."""
from runtime_compact.bridge_r4.contracts import (
    Packet, Side, Quality, RiskKind, FRESH, GAP, RESUME, decode_packet,
    encode_packet, COMMAND, STATUS, GENERATION, REASON, LEGACY_GENERATION,
    STAGING, ACTIVE, WORD_COUNT, MAX_AGE_US, BUSY, RX_OPEN, CONTROL_VALID,
    FORCE_OFF, ACTIVE_VALID, PROTOCOL_FAULT, MAILBOX_BASE, WINDOW_SIZE,
)

REASON_NAMES = {
    0: 'NONE', 1: 'WRITE', 2: 'COMMAND', 3: 'INCOMPLETE', 4: 'HEADER',
    5: 'PAYLOAD', 6: 'IDENTITY', 7: 'AGE', 8: 'QUALITY', 9: 'GAP',
    10: 'PRODUCER', 11: 'TIMEOUT', 12: 'LOCAL_HEALTH', 13: 'RUNTIME_PERMIT',
    14: 'LEGACY', 15: 'LATE', 16: 'BRIDGE_TIMEOUT',
}
READ_REGISTERS = frozenset((4, 8, 12, 16, 20, 24, 32, 40))


def allowed_read(offset):
    return (type(offset) is int and offset % 4 == 0 and
            (offset in READ_REGISTERS or 0x400 <= offset <= 0x57c))


def allowed_write(offset, value):
    return (type(offset) is int and type(value) is int and 0 <= value <= 0xffffffff
            and offset % 4 == 0 and
            ((offset == 0 and value in (1, 2, 3)) or 0x100 <= offset <= 0x27c))


def validate_control_wire(wire):
    packet = decode_packet(wire)
    # Match the adapter's candidate_control, not merely a valid observation.
    if not packet.control_candidate:
        raise ValueError('KPF1_NOT_COMPLETE_FRESH_CONTROL_CANDIDATE')
    if packet.capture_ns <= 0 or not 0 <= packet.age_us < MAX_AGE_US:
        raise ValueError('KPF1_CAPTURE_TIME_OR_AGE_INVALID')
    return packet
