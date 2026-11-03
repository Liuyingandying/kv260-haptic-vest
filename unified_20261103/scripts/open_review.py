"""Open only the new source-review project when the user requests the GUI."""
import json
import os
from pathlib import Path
import subprocess
from tool_paths import vivado, child_env

ROOT=Path(__file__).resolve().parents[1]
if not (ROOT/'vivado/LATEST_REVIEW.json').is_file():
    raise SystemExit('Run python scripts/create_review_project.py first')
receipt=json.loads((ROOT/'vivado/LATEST_REVIEW.json').read_text(encoding='utf8'))
project=Path(receipt['project'])
if receipt['exit_code'] or not project.is_relative_to((ROOT/'vivado').resolve()) or not project.is_file():
    raise SystemExit('No successfully created review project')
env=child_env()
subprocess.Popen([vivado(),'-mode','gui',str(project)],cwd=project.parent,env=env)
print(project)
