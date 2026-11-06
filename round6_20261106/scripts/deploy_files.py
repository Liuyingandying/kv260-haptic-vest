"""Upload an isolated, hash-checked new user directory. Does not run payload programs."""
from pathlib import Path
import argparse,hashlib,io,json,tarfile,uuid
import board_console
R=Path(__file__).resolve().parents[1]
def deploy(directory,tag,base_receipt=None):
 assert tag.replace('_','').isalnum() and directory.is_dir()
 files=[p for p in sorted(directory.rglob('*')) if p.is_file() and '__pycache__' not in p.parts]
 assert 0<len(files)<=512
 expected={p.relative_to(directory).as_posix():{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}
 base=json.loads(base_receipt.read_text(encoding='utf8')) if base_receipt else None
 changed=[p for p in files if not base or base['files'].get(p.relative_to(directory).as_posix())!=expected[p.relative_to(directory).as_posix()]]
 transmitted={p.relative_to(directory).as_posix():expected[p.relative_to(directory).as_posix()] for p in changed}
 out=io.BytesIO()
 with tarfile.open(fileobj=out,mode='w:gz') as t:
  for p in changed:t.add(p,arcname=p.relative_to(directory).as_posix(),recursive=False)
 payload=out.getvalue();assert len(payload)<=8*1024*1024
 nonce=uuid.uuid4().hex[:12];target='/home/ubuntu/kv260_r6_'+tag+'_'+nonce
 spec={'nonce':nonce,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
 template=(R/'scripts/upload_receiver_template.py.txt').read_text(encoding='utf8')
 if base:
  base_copy="""TARGET.mkdir(mode=0o700)
for rel,e in ALL_EXPECTED.items():
 if rel in contents:continue
 p=PurePosixPath(rel)
 assert not p.is_absolute() and '..' not in p.parts
 source=Path(BASE_TARGET)/rel
 assert source.is_file() and source.stat().st_size==e['bytes'] and hashlib.sha256(source.read_bytes()).hexdigest()==e['sha256']
 dest=TARGET/rel;dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 with dest.open('xb') as f:f.write(source.read_bytes())
 os.chmod(dest,0o700 if rel.endswith('.aarch64') else 0o600)
"""
  template=template.replace('TARGET.mkdir(mode=0o700)\n',base_copy)
  template=template.replace("result={'status':'PASS'", "for rel,e in ALL_EXPECTED.items():\n p=TARGET/rel\n assert p.stat().st_size==e['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==e['sha256']\nEXPECTED=ALL_EXPECTED\nresult={'status':'PASS'")
 source='from pathlib import Path\nTARGET=Path('+repr(target)+')\nSPEC='+repr(spec)+'\nEXPECTED='+repr(transmitted)+'\nALL_EXPECTED='+repr(expected)+'\nBASE_TARGET='+repr(base['target'] if base else None)+'\n'+template
 c=board_console.Console('deploy_'+tag+'_'+nonce)
 try:result=c.upload(source,'new_user_directory_only',payload,nonce)
 finally:c.close()
 with (R/'evidence'/('DEPLOY_'+tag+'_'+nonce+'.json')).open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
 print(json.dumps({'tag':tag,'target':target,'files':len(expected),'changed_files':len(changed),'bytes':len(payload),'package_sha256':spec['sha256']},ensure_ascii=False),flush=True)
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);p.add_argument('--tag',required=True);p.add_argument('--base-receipt',type=Path);a=p.parse_args();deploy(a.directory,a.tag,a.base_receipt)
