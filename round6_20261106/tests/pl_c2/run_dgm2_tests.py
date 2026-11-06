"""Narrow offline DGM2 RTL regression. No SDK, serial, MMIO or board access."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse, datetime, hashlib, json, re, shutil, struct, subprocess, sys, zlib

ROOT=Path(__file__).resolve().parents[2]
TEST=Path(__file__).resolve().parent
OUT=None
IVERILOG=VVP=None
RTL=ROOT/'fpga_c2/rtl/core/perception_axi_runtime_adapter.sv'
TB=TEST/'tb_dgm2_adapter.sv'
def block(index):return index//32+(8 if index%16>=8 else 0)
healthy=[index for index in range(256) if 0xf0f0&(1<<block(index))]
def packet(indices=healthy,unknown=bytes(32),quality=1,mask=0xf0f0,cap=1,legacy=False):
    words=[0x31444744 if legacy else 0x324d4744,1 if legacy else 2,384,100,1,0,0,0,0,quality,3,0,
           131 if legacy else mask,4 if legacy else cap,3 if legacy else 0,0,0,0,900000,1,640|(400<<16),96,32,256]
    matrix=bytes(int(index in indices) for index in range(256))
    blob=struct.pack('<24I',*words)+matrix+unknown
    return blob[:44]+struct.pack('<I',zlib.crc32(blob)&0xffffffff)+blob[48:]
def mutate(blob,word,value,recrc=True):
    data=bytearray(blob);struct.pack_into('<I',data,word*4,value)
    if recrc:
        data[44:48]=bytes(4);struct.pack_into('<I',data,44,zlib.crc32(data)&0xffffffff)
    return bytes(data)
CASES=[]
def add(name,blob,case=0,accept=True,mode=1):CASES.append((name,blob,case,accept,mode))
for count in (8,32,64,128):add('matrix_'+str(count),packet(healthy[:count]))
add('partial_unknown_retained',packet(unknown=bytes([1])+bytes(31)))
add('unknown_nonzero_rejected',packet(unknown=bytes(16)+bytes([1])+bytes(15)),accept=False)
add('offline_cell_rejected',packet([0]),accept=False)
add('offline_output_mask_rejected',packet(mask=0xffff),accept=False)
add('above_declared_cap_rejected',mutate(packet(),24+128//4,0x01010102),accept=False)
add('level5_rejected',mutate(packet(cap=4),24+128//4,0x01010105),accept=False)
add('CRC_corruption_rejected',mutate(packet(),11,0,False),accept=False)
add('all_unknown_rejected',packet([],bytes([255])*32),accept=False)
add('VALID_with_unknown_rejected',packet(unknown=bytes([1])+bytes(31),quality=0),accept=False)
add('explicit_zero_OFF_atomic',packet(),case=7)
add('health_loss_inhibits',packet(),case=9)
add('permit_loss_inhibits',packet(),case=10)
add('lease_expiry_inhibits',packet(),case=11)
add('duplicate_sequence_rejected',packet(),case=12)
add('permit_before_commit_no_grant',packet(),case=13)
add('legacy_DGD1_mode0_unchanged',packet([131],legacy=True),mode=0)
add('matrix_packet_rejected_by_mode0',packet(),accept=False,mode=0)
add('legacy_packet_rejected_by_matrix_mode',packet([131],legacy=True),accept=False)

def run(item):
    name,blob,case,accept,mode=item
    mem=OUT/(name+'.mem');mem.write_text(''.join(f'{word:08x}\n' for word in struct.unpack('<96I',blob)),encoding='ascii')
    vvp=OUT/(name+'.vvp')
    command=[IVERILOG,'-g2012','-s','tb_dgm2_adapter',
        '-Ptb_dgm2_adapter.CASE_ID='+str(case),'-Ptb_dgm2_adapter.MATRIX_MODE='+str(mode),
        '-Ptb_dgm2_adapter.EXPECT_ACCEPT='+str(int(accept)),'-o',str(vvp),str(TB),str(RTL)]
    result={'name':name,'expected_accept':accept,'matrix_mode':mode,'fixture_sha256':hashlib.sha256(blob).hexdigest(),
            'nonzero_count':sum(value!=0 for value in blob[96:352]),'compile_command':command,'status':'FAIL'}
    c=subprocess.run(command,capture_output=True,timeout=30)
    (OUT/(name+'.compile.stdout')).write_bytes(c.stdout);(OUT/(name+'.compile.stderr')).write_bytes(c.stderr)
    result['compile_exit']=c.returncode
    if c.returncode==0:
        command=[VVP,str(vvp),'+PACKET='+str(mem)]
        p=subprocess.run(command,capture_output=True,timeout=30)
        (OUT/(name+'.run.stdout')).write_bytes(p.stdout);(OUT/(name+'.run.stderr')).write_bytes(p.stderr)
        text=p.stdout.decode(errors='replace');match=re.search(r'checks=(\d+) failures=(\d+)',text)
        result.update(run_command=command,run_exit=p.returncode,stdout=text,checks=int(match[1]) if match else 0,
            status='PASS' if p.returncode==0 and 'DGM2_ADAPTER_SIM_PASS' in text else 'FAIL')
    return result
def main():
    global OUT,IVERILOG,VVP
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog');parser.add_argument('--vvp')
    args=parser.parse_args()
    IVERILOG=args.iverilog or shutil.which('iverilog');VVP=args.vvp or shutil.which('vvp')
    if not IVERILOG or not VVP:parser.error('iverilog and vvp must be on PATH or passed explicitly')
    expected='c9768e1ae0302f9a9c131e77780d093894e429b6d3a5d54d1246cef1a6d0fb13'
    if hashlib.sha256(RTL.read_bytes()).hexdigest()!=expected:
        parser.error('This suite is bound to the published original M2 adapter, not C3')
    OUT=TEST.parent/'results'/('DGM2_ADAPTER_RTL_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    OUT.mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(run,CASES))
    sources=[RTL,ROOT/'fpga_c2/rtl/core/unified_perception_haptic_runtime.sv',ROOT/'fpga_c2/rtl/kv260_unified_board_top.sv',TB]
    record={'status':'PASS' if all(row['status']=='PASS' for row in rows) else 'FAIL','cases':rows,
        'case_count':len(rows),'checks':sum(row.get('checks',0) for row in rows),'hardware_action_taken':False,
        'scope':'Adapter/CRC/atomic peer RAM model only; no physical PWM or hardware claim',
        'source_sha256':{str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}
    (OUT/'RESULT.json').write_text(json.dumps(record,indent=2),encoding='utf8')
    print(json.dumps({'status':record['status'],'cases':len(rows),'checks':record['checks'],'result':str(OUT/'RESULT.json'),
        'failed':[row for row in rows if row['status']!='PASS']},indent=2),flush=True)
    return 0 if record['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
