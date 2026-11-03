"""Decode local byte-preserved Vivado JSON into literal Tcl parameter lists.

Vivado splits long JSON string values into arrays of adjacent fragments, even
inside words. They must be concatenated WITHOUT spaces, never str(list).
This touches only generated Tcl; reference XCI/BD remain unchanged.
"""
from pathlib import Path
import json
import hashlib

ROOT = Path(__file__).resolve().parents[1]

def scalar(value):
    if isinstance(value, list):
        if not all(isinstance(part, str) for part in value):
            raise ValueError('Non-string fragmented value')
        return ''.join(value)
    if isinstance(value, bool):
        return '1' if value else '0'
    return str(value)

def literal(value):
    value = scalar(value)
    # The supplied XCI contains no unmatched brace literals. Keep bracket and
    # semicolon characters protected by braces, without Tcl substitutions.
    if '{' in value or '}' in value:
        raise ValueError('Brace in baseline value: needs an explicit decoder')
    return '{' + value + '}'

def main():
    bd_path=ROOT/'platform/reference_kv260_ps_bd.bd'
    xci_path=ROOT/'platform/reference_ps.xci'
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (bd_path,xci_path)}
    bd=json.loads(bd_path.read_text())['design']
    raw=json.loads(xci_path.read_text())['ip_inst']['parameters']['component_parameters']
    values={key:scalar(rows[0]['value']) for key,rows in raw.items()}
    explicit=bd['components']['zynq_ultra_ps_e_0']['parameters']
    lines=['# Generated from preserved XCI/BD. Long JSON fragments concatenate without separators.',
           'set ps_baseline_explicit [list \\']
    for key in explicit:
        lines.append('  '+literal('CONFIG.'+key)+' '+literal(values[key])+' \\')
    lines.extend([']','set ps_baseline_all [list \\'])
    for key,value in values.items():
        lines.append('  '+literal('CONFIG.'+key)+' '+literal(value)+' \\')
    lines.append(']')
    target=ROOT/'platform/ps_baseline_parameters.tcl'
    target.write_text('\n'.join(lines)+'\n',encoding='utf8')
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (bd_path,xci_path)}
    assert before==after
    result=dict(explicit=len(explicit),full=len(values),
                fragmented_parameters=[k for k,v in raw.items() if isinstance(v[0]['value'],list)],
                separator='',reference_files_unchanged=True,
                generated_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    (ROOT/'evidence').mkdir(parents=True,exist_ok=True)
    (ROOT/'evidence/PS_JSON_DECODING_RESULT.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result))

if __name__=='__main__':main()
