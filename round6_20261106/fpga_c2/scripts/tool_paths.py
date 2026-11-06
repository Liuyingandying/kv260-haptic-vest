"""Local development tools: explicit environment, then PATH, then legacy fallback.

Only child-process environment is adjusted. This module never changes system
settings, installs software, or opens any hardware interface.
"""
import os
from pathlib import Path
import platform
import shutil


def find_tool(variable, names, fallbacks=()):
    explicit = os.environ.get(variable)
    if explicit:
        candidate = Path(explicit).expanduser()
        found = str(candidate.resolve()) if candidate.is_file() else shutil.which(explicit)
        if not found:
            raise FileNotFoundError(f'{variable} points to an unavailable executable: {explicit}')
        return found
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for name in fallbacks:
        if Path(name).is_file():
            return str(Path(name).resolve())
    raise FileNotFoundError(f'Set {variable} or put {" / ".join(names)} on PATH')


def vivado():
    return find_tool('VIVADO_BAT', ('vivado.bat', 'vivado'),
                     (r'E:\vivado\2025.2\Vivado\bin\vivado.bat',))


def cpp_compiler():
    return find_tool('CXX', ('g++', 'clang++'),
                     (r'D:\02_Toolchains\MinGW-w64\bin\g++.exe',))


def iverilog():
    return find_tool('IVERILOG_EXE' if os.environ.get('IVERILOG_EXE') else 'IVERILOG', ('iverilog', 'iverilog.exe'),
                     (r'C:\iverilog\bin\iverilog.exe',))


def vvp():
    return find_tool('VVP_EXE' if os.environ.get('VVP_EXE') else 'VVP', ('vvp', 'vvp.exe'),
                     (r'C:\iverilog\bin\vvp.exe',))


def child_env():
    env = dict(os.environ)
    env.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    env.setdefault('PYTHONUTF8', '1')
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    if os.name == 'nt' and not env.get('PROCESSOR_ARCHITECTURE'):
        machine = platform.machine().upper()
        env['PROCESSOR_ARCHITECTURE'] = machine if machine in ('AMD64', 'ARM64', 'X86') else 'AMD64'
    return env


if __name__ == '__main__':
    import json
    report = {}
    for name, getter in (('CXX', cpp_compiler), ('IVERILOG_EXE', iverilog),
                         ('VVP_EXE', vvp), ('VIVADO_BAT', vivado)):
        try:
            report[name] = {'status': 'FOUND', 'path': getter()}
        except FileNotFoundError as exc:
            report[name] = {'status': 'MISSING', 'reason': str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
