"""Open the new completed board project, never Hardware Manager or a device."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent/'scripts'))
from tool_paths import vivado as find_vivado, child_env
if not (ROOT/'LATEST_BUILD.json').is_file():
    raise SystemExit('Run python board_20261103/scripts/build_board.py --stage project first; historical reports are not local projects')
receipt=json.loads((ROOT/'LATEST_BUILD.json').read_text(encoding='utf8'))
if receipt.get('status')!='PASS':
    raise SystemExit('Board build has not passed; see LATEST_BUILD.json and build logs')
project=Path(receipt['project'])
if not project.is_relative_to((ROOT/'projects').resolve()) or not project.is_file():
    raise SystemExit('Rebuild in this checkout; do not open a historical absolute project path')
env=child_env()
subprocess.Popen([find_vivado(),'-mode','gui',str(project)],cwd=project.parent,env=env)
print(project)
