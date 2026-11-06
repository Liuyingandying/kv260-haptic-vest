"""Isolated optional native JSON parser; no SDK, serial or device access.

 The complete geometry contract is validated afterwards exactly as before.
 No parse-error retry with another parser: stricter UTF8/NaN rejection remains
 a rejection. GEMINI_JSON_PARSER=stdlib is an explicit benchmark/rollback mode.
"""
from pathlib import Path
import json, os, platform, sys

_mode=os.environ.get('GEMINI_JSON_PARSER','auto')
if _mode not in ('auto','stdlib'):raise ValueError('JSON_PARSER_MODE_MUST_BE_AUTO_OR_STDLIB')
_native=None
_path=None
if _mode=='auto':
    machine=platform.machine().lower()
    tag=('cp312_aarch64' if sys.version_info[:2]==(3,12) and machine in ('aarch64','arm64') and sys.platform.startswith('linux') else
         'cp313_win_amd64' if sys.version_info[:2]==(3,13) and machine in ('amd64','x86_64') and sys.platform=='win32' else None)
    if tag:
        _path=Path(__file__).resolve().parent/'optional'/tag
        if _path.is_dir():
            sys.path.insert(0,str(_path))
            try:import orjson as _native
            finally:sys.path.remove(str(_path))

def loads_native(data):
    if not isinstance(data,bytes):raise ValueError('NATIVE_JSON_BYTES_REQUIRED')
    return _native.loads(data) if _native is not None else json.loads(data)

def parser_info():
    return {'implementation':'orjson' if _native is not None else 'stdlib.json',
            'version':_native.__version__ if _native is not None else sys.version.split()[0],
            'selection':_mode,'isolated_path':str(_path) if _native is not None else None,
            'full_geometry_validator_retained':True}
