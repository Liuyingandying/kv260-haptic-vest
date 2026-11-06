"""Explicit Linux/AArch64 native32 transport. No hardware access on import.

Profile identity is caller supplied, never inferred from a filename. The GPIO
ID is a design identifier; file hashes do not attest all live configuration bits.
"""
from dataclasses import dataclass
from pathlib import Path
import ctypes
import hashlib
import mmap
import os
import platform
import re
import struct
import sys

WINDOW = 0x10000
BASES = {'mailbox': 0x9f000000, 'gpio': 0x9e000000, 'status': 0x9c000000}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class EnvironmentProfile:
    expected_firmware_name: str
    dt_firmware_property: str
    installed_bitstream_path: str
    installed_bitstream_sha256: str
    native_helper_path: str
    native_helper_sha256: str
    expected_design_id: int

    def validate(self):
        if self.expected_design_id not in (0x52364331, 0x52364332, 0x52364431, 0x52364d32):
            raise ValueError('EXPLICIT_REVIEWED_C1_C2_D1_DGM2_DESIGN_ID_REQUIRED')
        if not self.expected_firmware_name or '\x00' in self.expected_firmware_name:
            raise ValueError('EXPLICIT_FIRMWARE_NAME_REQUIRED')
        for name in ('installed_bitstream_sha256', 'native_helper_sha256'):
            if not re.fullmatch('[a-f0-9]{64}', getattr(self, name)):
                raise ValueError('EXPLICIT_SHA256_REQUIRED:'+name)
        for name in ('dt_firmware_property', 'installed_bitstream_path', 'native_helper_path'):
            if not Path(getattr(self, name)).is_absolute():
                raise ValueError('ABSOLUTE_PROFILE_PATH_REQUIRED:'+name)


def inspect_environment(profile, *, sysfs=Path('/sys'), dev=Path('/dev'),
                        boot_id_path=Path('/proc/sys/kernel/random/boot_id')):
    profile.validate()
    managers = list((sysfs/'class/fpga_manager').glob('fpga*'))
    if len(managers) != 1 or (managers[0]/'state').read_text().strip() != 'operating':
        raise RuntimeError('FPGA_MANAGER_NOT_UNIQUE_OPERATING')
    raw = Path(profile.dt_firmware_property).read_bytes()
    if raw.rstrip(b'\x00').decode('ascii') != profile.expected_firmware_name:
        raise RuntimeError('FIRMWARE_NAME_MISMATCH')
    if sha(profile.installed_bitstream_path) != profile.installed_bitstream_sha256:
        raise RuntimeError('INSTALLED_BITSTREAM_HASH_MISMATCH')
    if sha(profile.native_helper_path) != profile.native_helper_sha256:
        raise RuntimeError('CONTROL_NATIVE_HELPER_HASH_MISMATCH')
    hdr = Path(profile.native_helper_path).read_bytes()[:64]
    if hdr[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<HH', hdr, 16) != (3, 183):
        raise RuntimeError('NATIVE_HELPER_NOT_AARCH64_ELF64_LE_DYN')
    devices = {}
    for key, base in BASES.items():
        matches = []
        for uio in (sysfs/'class/uio').glob('uio*'):
            if not re.fullmatch('uio[0-9]+', uio.name):
                continue
            mp = uio/'maps/map0'
            try:
                fields = tuple(int((mp/n).read_text().strip(), 0) if (mp/n).exists() else 0
                               for n in ('addr', 'size', 'offset'))
            except (ValueError, OSError):
                continue
            if fields == (base, WINDOW, 0):
                matches.append(str(dev/uio.name))
        if len(matches) != 1:
            raise RuntimeError('UIO_MAPPING_NOT_UNIQUE:'+key)
        devices[key] = matches[0]
    return {'devices': devices, 'bases': BASES, 'window_bytes': WINDOW,
            'boot_id': boot_id_path.read_text().strip(),
            'firmware_name': profile.expected_firmware_name,
            'installed_bitstream_sha256': profile.installed_bitstream_sha256,
            'native_helper_sha256': profile.native_helper_sha256,
            'expected_design_id': profile.expected_design_id}


class _NativeTransport:
    hardware_access = True
    physical_output_permitted = False

    def __init__(self, profile, environment):
        self.maps, self.anchors, self.bases, self.fds = {}, {}, {}, {}
        try:
            self.lib = ctypes.CDLL(profile.native_helper_path)
            for name in ('control_read32', 'control_gpio_read32', 'control_status_read32'):
                fn = getattr(self.lib, name)
                fn.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
                fn.restype = ctypes.c_int
            self.lib.control_write32.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
            self.lib.control_write32.restype = ctypes.c_int
            for key, path in environment['devices'].items():
                # GPIO/status use read-only fd? ctypes.from_buffer needs a
                # writable mapping anchor, so map O_RDWR but expose NO writes.
                self.fds[key] = os.open(path, os.O_RDWR | os.O_SYNC)
                self.maps[key] = mmap.mmap(self.fds[key], WINDOW, flags=mmap.MAP_SHARED,
                                          prot=mmap.PROT_READ | mmap.PROT_WRITE, offset=0)
                self.anchors[key] = ctypes.c_char.from_buffer(self.maps[key])
                self.bases[key] = ctypes.addressof(self.anchors[key])
            actual = self._read('gpio', 0, 'control_gpio_read32')
            if actual != profile.expected_design_id:
                raise RuntimeError('LIVE_GPIO_DESIGN_ID_MISMATCH:'+hex(actual))
            self.design_id = actual
        except BaseException:
            self.close()
            raise

    def _read(self, key, offset, function):
        value = ctypes.c_uint32()
        rc = getattr(self.lib, function)(self.bases[key], offset, ctypes.byref(value))
        if rc:
            raise RuntimeError('NATIVE32_READ_GUARD:'+str(rc))
        return value.value

    def read32(self, offset):
        return self._read('mailbox', offset, 'control_read32')

    def read_status32(self, offset):
        return self._read('status', offset, 'control_status_read32')

    def write32(self, offset, value):
        rc = self.lib.control_write32(self.bases['mailbox'], offset, value)
        if rc:
            raise RuntimeError('NATIVE32_WRITE_GUARD:'+str(rc))

    def close(self):
        self.anchors.clear()
        for mapping in self.maps.values():
            mapping.close()
        self.maps.clear()
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()
        self.bases.clear()


def open_confirmed_transport(profile):
    if sys.platform != 'linux' or platform.machine().lower() not in ('aarch64', 'arm64'):
        raise RuntimeError('REAL_CONTROL_REQUIRES_LINUX_AARCH64')
    first = inspect_environment(profile)
    if inspect_environment(profile) != first:
        raise RuntimeError('PROFILE_OR_UIO_CHANGED_BEFORE_OPEN')
    return _NativeTransport(profile, first)
