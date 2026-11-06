"""R6 guarded open-drain digital I2C diagnostics. Inert on import.

Does not use the haptic scheduler, SDK, camera or physical motor power.
Requires the R6 GPIO diagnostic-owner RTL contract and an exact image profile.
MMIO failure latches a stop; no retries or subsequent hardware cleanup writes.
The parent must enforce a wall-time bound: an unreturned MMIO cannot be safely
cancelled from Python. A one-run marker prevents an accidental automatic retry.
"""
from pathlib import Path
import argparse, ctypes, hashlib, json, mmap, os, platform, re, sys, time

ARM=1<<30
OWNER=1<<25
SCL_LOW=1<<26
SDA_LOW=1<<27
BUSY=1<<24
PAGES=15
ALLOWED=ARM|OWNER|SCL_LOW|SDA_LOW|BUSY|PAGES
NATIVE_SHA='356122bb910cfe28f3f423108c5bbc000ef716e18e0c1c0b6ca76bccc7ecb365'
R6_GPIO_ID=0x52364331
EXPECTED_FW="xilinx/kv260-round6-fullctrl-20261105/kv260_round6_fullctrl.bit.bin"
assert not ALLOWED&(1<<23), "DIAGNOSTIC_MUST_NOT_ENABLE_CONTROL"
PCA_ADDRESSES=(0x4d,0x4e,0x4f,0x44,0x49,0x4a,0x4b,0x4c,
               0x40,0x41,0x42,0x43,0x45,0x46,0x47,0x48)

def require(ok,reason):
    if not ok:raise RuntimeError(reason)

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class Nack(RuntimeError):
    def __init__(self,stage,index,value):
        self.stage,self.index,self.value=stage,index,value
        super().__init__(f'NACK stage={stage} byte_index={index} sent=0x{value:02x}')

class Native32:
    def __init__(self,memory,library):
        require(sha(library)==NATIVE_SHA,'NATIVE_HELPER_HASH_CHANGED')
        self.lib=ctypes.CDLL(str(library));self.view=ctypes.c_char.from_buffer(memory)
        self.pointer=ctypes.addressof(self.view)
        self.lib.sig2_read32.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint32)]
        self.lib.sig2_read32.restype=ctypes.c_int
        self.lib.sig2_write32.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32]
        self.lib.sig2_write32.restype=ctypes.c_int
    def read(self,offset):
        v=ctypes.c_uint32();require(self.lib.sig2_read32(self.pointer,offset,ctypes.byref(v))==0,'NATIVE_READ_REJECTED')
        return v.value
    def write(self,offset,value):
        require(self.lib.sig2_write32(self.pointer,offset,value)==0,'NATIVE_WRITE_REJECTED')
    def close(self):self.pointer=0;self.view=None

class GuardPins:
    """Maps only the reviewed GPIO words; no arbitrary physical address access."""
    def __init__(self,io,expected_id,trace,now=time.monotonic_ns,pause=time.sleep):
        self.io,self.expected_id,self.trace=io,expected_id,trace
        self.now,self.pause=now,pause
        self.data=0;self.tri=0xffffffff;self.failed=False;self.armed=False
    def rd(self,offset):
        require(not self.failed,'MMIO_FAILURE_LATCHED_NO_MORE_ACCESSES')
        require(offset in (0,8,12),'READ_OFFSET_DENIED')
        try:return self.io.read(offset)
        except BaseException:
            self.failed=True;raise
    def wr(self,offset,value):
        require(not self.failed,'MMIO_FAILURE_LATCHED_NO_MORE_ACCESSES')
        require(type(value)is int and 0<=value<=0xffffffff,'U32_REQUIRED')
        require((offset==8 and not(value&~ALLOWED)) or
                (offset==12 and value|ALLOWED==0xffffffff),'GPIO_WRITE_DENIED')
        try:self.io.write(offset,value)
        except BaseException:
            self.failed=True;raise
    def page(self,n):
        require(0<=n<=15,'PAGE_DENIED');self.data=(self.data&~PAGES)|n
        self.wr(8,self.data)
        if self.tri&PAGES:
            self.tri&=~PAGES;self.wr(12,self.tri)
        require(self.rd(8)&PAGES==n,'PAGE_SELECTOR_NOT_READ_BACK')
        return self.rd(0)
    def state(self):return self.page(14)
    def invariant(self):
        word=self.rd(8)
        require(word&(1<<11) and not word&(1<<12),'OUTPUT_INHIBIT_LOST')
        return {'word':word,'scl':bool(word&64),'sda':bool(word&128)}
    def open(self):
        self.trace('FIRST_GPIO_READ_BEGIN',address='0x9E00000C',width=32)
        require(self.rd(12)==0xffffffff,'FRESH_ALL_INPUTS_REQUIRED')
        self.trace('FIRST_GPIO_READ_RETURNED')
        require(self.rd(0)==self.expected_id,'R6_IMAGE_ID_WRONG')
        initial=self.invariant();st=self.state()
        require(st&15==0 and not(st&0x90),'GUARD_NOT_FRESH_OFF')
        self.data=OWNER;self.wr(8,self.data)
        self.tri=0xffffffff&~ALLOWED;self.wr(12,self.tri)
        # Owner is captured only while OFF. ARM remains low until checked.
        end=self.now()+100_000_000
        while self.now()<end:
            owner=self.page(15)
            if owner&1:break
            self.pause(.001)
        require(owner&1,'DIAGNOSTIC_OWNER_NOT_LATCHED')
        self.trace('DIAG_OWNER_LOCKED',page15=owner,initial=initial)
        self.data|=ARM;self.wr(8,self.data);self.armed=True
        end=self.now()+1_000_000_000
        while self.now()<end:
            st=self.state()
            if st&15 in (8,10):break
            self.pause(.001)
        require(st&15==8 and st&64 and not st&128,'GUARD_DID_NOT_ACQUIRE_RUN')
        owner=self.page(15)
        require(owner&1 and not owner&32 and owner&0xc0==0xc0,'DIAG_OWNER_OR_BUSINESS_ISOLATION_WRONG')
        self.invariant();self.trace('DIGITAL_BUS_ACQUIRED',guard=st)
    def transaction(self,active):
        self.data=(self.data|BUSY) if active else self.data&~BUSY
        self.wr(8,self.data)
    def drive(self,scl_low,sda_low):
        # Guard owns pad enables and forcibly releases on expiry/fault. The
        # low intents cannot enable motor outputs or select runtime control.
        self.data=(self.data&~(SCL_LOW|SDA_LOW))|(SCL_LOW if scl_low else 0)|(SDA_LOW if sda_low else 0)
        self.wr(8,self.data)
    def sense(self):
        w=self.rd(8)
        require(w&(1<<11) and not w&(1<<12),'OUTPUT_INHIBIT_LOST')
        return bool(w&64),bool(w&128)
    def close(self):
        require(not self.failed,'NO_HARDWARE_CLEANUP_AFTER_MMIO_FAILURE')
        self.drive(False,False);self.transaction(False)
        self.data&=~ARM;self.wr(8,self.data);self.armed=False
        end=self.now()+500_000_000
        while self.now()<end:
            st=self.state()
            if st&15 in (0,10) and not st&0x70:break
            self.pause(.001)
        require(st&15==0 and not st&0xf0 and st&4096,'GUARD_NOT_CLEAN_OFF')
        self.data=0;self.wr(8,0);self.wr(12,0xffffffff);self.tri=0xffffffff
        require(self.rd(12)==0xffffffff,'GPIO_DIRECTIONS_NOT_RESTORED')
        final=self.invariant();self.trace('DIGITAL_BUS_RELEASED',guard=st,final=final)
        return {'guard':st,'pins':final,'physical_pwm_closed':'NOT_VERIFIED'}

class BitBang:
    """I2C start/repeated-start/ACK/read/STOP; OD=low or release only."""
    def __init__(self,pins,trace,half_period_s=.000025,timeout_s=.02,
                 now=time.monotonic,pause=time.sleep,session_budget_s=35):
        require(.00001<=half_period_s<=.001,'HALF_PERIOD_OUT_OF_RANGE')
        self.p,self.trace,self.half,self.timeout=pins,trace,half_period_s,timeout_s
        self.now,self.pause=now,pause;self.deadline=now()+session_budget_s
        self.scl=False;self.sda=False;self.active=False
    def delay(self):
        require(self.now()<self.deadline,'SESSION_BUDGET_EXCEEDED');self.pause(self.half)
    def set(self,scl,sda):
        self.scl,self.sda=scl,sda;self.p.drive(scl,sda)
    def high(self):
        self.set(False,self.sda);end=self.now()+self.timeout
        while True:
            scl,sda=self.p.sense()
            if scl:return sda
            require(self.now()<end,'SCL_STRETCH_OR_GUARD_RELEASE_TIMEOUT')
            self.pause(self.half)
    def start(self,repeated=False):
        if not repeated:
            self.p.transaction(True);self.active=True
        self.set(True,False);self.delay();self.high();self.delay()
        require(self.p.sense()[1],'SDA_NOT_RELEASED_AT_START')
        self.set(False,True);self.delay();self.set(True,True)
        self.trace('IIC_REPEATED_START' if repeated else 'IIC_START')
    def stop(self):
        self.set(True,True);self.delay();self.high();self.delay()
        self.set(False,False);self.delay();scl,sda=self.p.sense()
        require(scl and sda,'STOP_DID_NOT_RELEASE_BOTH_LINES')
        self.p.transaction(False);self.active=False;self.trace('IIC_STOP',scl=scl,sda=sda)
    def write_byte(self,value,stage,index):
        require(type(value)is int and 0<=value<=255,'BYTE_REQUIRED')
        self.trace('IIC_BYTE_BEGIN',stage_name=stage,byte_index=index,sent=value)
        for bit in range(7,-1,-1):
            low=not bool(value&(1<<bit));self.set(True,low);self.delay()
            actual=self.high();self.delay()
            if not low:require(actual,'IIC_ARBITRATION_OR_SDA_STUCK_LOW')
            self.set(True,low)
        self.set(True,False);self.delay();ack=not self.high();self.delay();self.set(True,False)
        self.trace('IIC_ACK',stage_name=stage,byte_index=index,sent=value,ack=ack,
                   scope='SDA_SAMPLED_DURING_NINTH_CLOCK_NOT_EXTERNAL_WAVEFORM')
        if not ack:raise Nack(stage,index,value)
    def read_byte(self,last,index):
        self.trace('IIC_READ_BYTE_BEGIN',byte_index=index,last=last)
        v=0
        for _ in range(8):
            self.set(True,False);self.delay();v=(v<<1)|int(self.high());self.delay();self.set(True,False)
        self.set(True,not last);self.delay();self.high();self.delay();self.set(True,False)
        self.trace('IIC_READ_BYTE',byte_index=index,value=v,master_ack=not last)
        return v
    def transfer(self,address,write=(),read_n=0):
        require(type(address)is int and 0<=address<128,'ADDRESS_7BIT_REQUIRED')
        require(type(read_n)is int and 0<=read_n<=64,'READ_LENGTH_DENIED')
        require(write or read_n,'EMPTY_TRANSFER_DENIED')
        require(len(write)<=66,'WRITE_LENGTH_DENIED')
        self.trace('IIC_TRANSACTION_BEGIN',address=address,write=list(write),read_n=read_n)
        try:
            self.start()
            if write:
                self.write_byte(address<<1,'ADDRESS_W',0)
                for n,b in enumerate(write):self.write_byte(b,'WRITE_DATA',n+1)
            if read_n:
                if write:self.start(repeated=True)
                self.write_byte((address<<1)|1,'ADDRESS_R',0)
                result=[self.read_byte(n==read_n-1,n) for n in range(read_n)]
            else:result=[]
            self.stop();self.trace('IIC_TRANSACTION_DONE',address=address,read=result)
            return result
        except Nack:
            # NACK is a known, returned protocol outcome, unlike unknown MMIO
            # failure. Emit one STOP then permit the next different target.
            self.stop();raise

class Inventory:
    """Sixteen configured targets, each individually read, no inferred values."""
    def __init__(self,bus,trace):self.bus,self.trace=bus,trace
    def select(self,channel):
        mask=1<<channel;self.bus.transfer(0x70,[mask])
        actual=self.bus.transfer(0x70,read_n=1)[0]
        require(actual==mask,'TCA_CHANNEL_REGISTER_MISMATCH')
        return actual
    def read_reg(self,address,reg,count=1):return self.bus.transfer(address,[reg],count)
    def init_full_off(self,address):
        # Exact legacy 50Hz configuration, only FULL_OFF PWM values. Never
        # issues a nonzero duty. Stop on first known NACK for this target.
        for reg,payload in [(0x00,[0x30]),(0xfe,[0x79]),
                            (0x06,[0,0,0,0x10]*16),(0x01,[0x04]),(0x00,[0x20])]:
            self.bus.transfer(address,[reg]+payload)
        self.bus.pause(.001)
        self.bus.transfer(address,[0x00,0xa0])
    def device(self,block,initialize=False):
        address=PCA_ADDRESSES[block];channel=block//4
        row={'block':block,'tca_channel':channel,'pca_7bit_address':address,
             'status':'NOT_VERIFIED','physical_identity':'NOT_VERIFIED','registers':{},
             'initialization_requested':initialize,'nonzero_PWM_requested':False}
        self.trace('DEVICE_BEGIN',**row)
        try:
            row['tca_readback']=self.select(channel)
            initial={k:self.read_reg(address,r)[0] for k,r in [('MODE1',0),('MODE2',1),('PRE_SCALE',0xfe)]}
            row['before_registers']=initial
            if initialize:self.init_full_off(address)
            after={k:self.read_reg(address,r)[0] for k,r in [('MODE1',0),('MODE2',1),('PRE_SCALE',0xfe)]}
            # AI is read from hardware, not assumed. Without AI read each
            # address separately so one register cannot masquerade as 64.
            if after['MODE1']&0x20:pwm=self.read_reg(address,0x06,64)
            else:pwm=[self.read_reg(address,r)[0] for r in range(0x06,0x46)]
            row.update(registers=after,pwm_registers=pwm,
                       full_off_register_bits=all(pwm[n+3]&0x10 for n in range(0,64,4)),
                       status='REGISTER_READBACK_COMPLETE')
            if initialize:
                # RESTART can be hardware-cleared. Compare persistent mode
                # bits (AI=1,SLEEP/ALLCALL=0), MODE2 and PRE_SCALE exactly.
                valid=(after['MODE1']&0x31)==0x20 and after['MODE2']==0x04 and after['PRE_SCALE']==0x79
                row['initialization_readback_matches']=valid and pwm==[0,0,0,0x10]*16
                if not row['initialization_readback_matches']:row['status']='REGISTER_CONFIGURATION_MISMATCH'
        except Nack as e:
            row.update(status='NACK',nack={'stage':e.stage,'byte_index':e.index,'sent':e.value,'message':str(e)},
                       physical_cause='UNKNOWN_NOT_PROVEN_ABSENT_OR_DAMAGED')
        self.trace('DEVICE_DONE',**row);return row
    def run(self,initialize=False):
        # Deselect first to isolate ALLCALL and duplicate addresses behind mux.
        self.bus.transfer(0x70,[0])
        rows=[self.device(n,initialize) for n in range(16)]
        self.bus.transfer(0x70,[0])
        return rows

    @staticmethod
    def missing_branches(rows):
        """Only four independently observed PCA address-W NACKs qualify.

        A TCA selection error, register/data NACK, partial result, or unrelated
        failure is not evidence of a missing configured-address branch.
        """
        require(len(rows)==16 and {r['block'] for r in rows}==set(range(16)),
                'COMPLETE_INITIAL_INVENTORY_REQUIRED')
        by_block={r['block']:r for r in rows};missing=[]
        for ch in range(4):
            target=[by_block[b] for b in range(ch*4,ch*4+4)]
            if all(r.get('status')=='NACK' and r.get('nack',{}).get('stage')=='ADDRESS_W'
                   and r['nack'].get('byte_index')==0
                   and r['nack'].get('sent')==PCA_ADDRESSES[r['block']]<<1
                   and r.get('tca_readback')==1<<ch for r in target):missing.append(ch)
        return missing

    def scan_missing(self,rows,authorized_channels):
        channels=self.missing_branches(rows)
        # The current inventory must reproduce the prior qualifying outcome.
        # An explicit new one-shot scan action is not an automatic retry.
        require(channels and set(channels).issubset(set(authorized_channels)),
                'CURRENT_MISSING_BRANCHES_NOT_PRIOR_AUTHORIZED')
        scans=[]
        for channel in channels:
            selector=self.select(channel)
            for address in range(0x40,0x50):
                row={'tca_channel':channel,'selector_readback':selector,'address_7bit':address,
                     'address_byte':(address<<1)|1,'ack_stage':'ADDRESS_R',
                     'configured_for_this_branch':address in PCA_ADDRESSES[channel*4:channel*4+4],
                     'probe':'ADDRESS_R_THEN_ONE_BYTE_MASTER_NACK_STOP',
                     'register_configuration_written':False,'physical_identity':'UNKNOWN'}
                self.trace('SCAN_ADDRESS_BEGIN',**row)
                try:
                    data=self.bus.transfer(address,read_n=1)
                    row.update(ack=True,raw_current_pointer_byte=data[0],
                               byte_meaning='UNINTERPRETED_CURRENT_POINTER_NOT_MODE1',
                               status='ADDRESS_ACK_READ_BYTE_RETURNED')
                except Nack as e:
                    row.update(ack=False,status='NACK',
                               nack={'stage':e.stage,'byte_index':e.index,'sent':e.value,'message':str(e)})
                self.trace('SCAN_ADDRESS_DONE',**row);scans.append(row)
        self.bus.transfer(0x70,[0])
        return {'authorized_prior_channels':list(authorized_channels),'current_scanned_channels':channels,
                'probes':scans,'probes_count':len(scans),'per_address_attempts':1,
                'physical_cause':'UNKNOWN','interpretation':'Read ACK is evidence of a responding address, not device serial identity or a physical wiring diagnosis.'}


def prior_inventory_gate(path,profile,boot_id):
    require(path is not None,'EXPLICIT_PRIOR_C1_INVENTORY_RESULT_REQUIRED')
    prior=json.loads(Path(path).read_text())
    require(prior.get('schema')=='r6c1_iic_inventory_v1','PRIOR_MUST_BE_C1_INVENTORY')
    require(prior.get('boot_id')==boot_id,'PRIOR_BOOT_CHANGED')
    pp=prior.get('profile',{})
    require(pp.get('bin_sha256')==profile['bin_sha256'] and
            pp.get('gpio_build_id')==profile['gpio_build_id'],'PRIOR_C1_BIN_OR_ID_CHANGED')
    require(prior.get('action')=='inventory' and prior.get('result')=='FAIL_PARTIAL_DEVICES',
            'PRIOR_MUST_BE_RETURNED_PARTIAL_INVENTORY_NOT_UNKNOWN_FAILURE')
    require(prior.get('terminal',{}).get('guard',0)&15==0 and
            prior['terminal']['guard']&4096,'PRIOR_CLEAN_OFF_REQUIRED')
    channels=Inventory.missing_branches(prior.get('devices',[]))
    require(channels,'NO_PRIOR_QUALIFYING_MISSING_BRANCH')
    return channels

def profile_gate(profile):
    require(platform.machine()=='aarch64' and os.geteuid()==0,'EXISTING_ROOT_AARCH64_REQUIRED')
    require(profile.get('schema')=='r6_guarded_iic_profile_v1','EXACT_R6_PROFILE_REQUIRED')
    require(int(profile.get('gpio_build_id','0'),0)==R6_GPIO_ID,'EXACT_R6C1_ID_REQUIRED')
    require(re.fullmatch('[0-9a-f]{64}',profile.get('bin_sha256','')) is not None,'BIN_SHA_REQUIRED')
    require(Path(profile['installed_bin']).is_absolute(),'ABSOLUTE_INSTALLED_BIN_REQUIRED')
    fw=Path('/sys/firmware/devicetree/base/fpga-region/firmware-name').read_bytes().rstrip(b'\0').decode()
    require(fw==profile['firmware_name']==EXPECTED_FW,'EXACT_C1_FIRMWARE_REQUIRED')
    require(sha(profile['installed_bin'])==profile['bin_sha256'],'INSTALLED_BIN_HASH_CHANGED')
    require(Path('/sys/class/fpga_manager/fpga0/state').read_text().strip()=='operating','FPGA_NOT_OPERATING')
    clock=next((line for line in Path('/sys/kernel/debug/clk/clk_summary').read_text().splitlines()
                if line.split() and line.split()[0]=='pl0_ref'),None)
    require(clock is not None and 90_000_000<=int(clock.split()[4])<=110_000_000,'PL_CLOCK_NOT_90_110MHZ')
    found=[]
    for p in Path('/sys/class/uio').glob('uio*'):
        m=p/'maps/map0'
        if int((m/'addr').read_text(),0)==0x9e000000:
            require(int((m/'size').read_text(),0)==65536,'GPIO_WINDOW_CHANGED')
            require((p/'name').read_text().strip()==profile['uio_name'],'GPIO_UIO_NAME_CHANGED')
            found.append('/dev/'+p.name)
    require(len(found)==1,'GPIO_UIO_NOT_UNIQUE')
    # Complete root-visible ownership scan. Never infer idle from denied paths.
    for p in Path('/proc').glob('[0-9]*'):
        try:
            for fd in (p/'fd').iterdir():
                try:dest=os.readlink(fd)
                except FileNotFoundError:continue
                require(not re.match(r'^/dev/(uio\d+|mem)(\s|$)',dest),'EXISTING_MMIO_OWNER:'+str(fd))
            require(not re.search(r'/dev/(uio\d+|mem)(\s|$)',(p/'maps').read_text()),'EXISTING_MMIO_MAP:'+str(p))
        except FileNotFoundError:pass
    return found[0]

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--run-once',action='store_true');p.add_argument('--vplus-disconnected',action='store_true')
    p.add_argument('--initialize-full-off',action='store_true')
    p.add_argument('--scan-missing-branches',action='store_true')
    p.add_argument('--prior-inventory-result',type=Path)
    args=p.parse_args(argv)
    if not(args.run_once and args.vplus_disconnected):p.error('--run-once --vplus-disconnected required')
    args.out.mkdir(exist_ok=False,mode=0o700)
    stream=(args.out/'metadata.jsonl').open('x',encoding='utf8',buffering=1)
    def trace(stage,**fields):
        line=json.dumps({'monotonic_ns':time.monotonic_ns(),'stage':stage,**fields})
        stream.write(line+'\n');print(line,flush=True)
    fd=memory=io=pins=None
    action='scan_missing_branches' if args.scan_missing_branches else 'inventory'
    result={'schema':'r6c1_iic_inventory_v1','action':action,'result':'BLOCKED','hardware_action_taken':False,
            'physical_pwm':'NOT_VERIFIED','physical_vibration':'NOT_TESTED','independent_shutdown':'NOT_VERIFIED'}
    try:
        profile=json.loads(args.profile.read_text());node=profile_gate(profile)
        boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        prior_channels=prior_inventory_gate(args.prior_inventory_result,profile,boot) if args.scan_missing_branches else []
        # Distinct explicit C1 actions, each limited once per boot and image.
        # Changing output names does not bypass this action marker.
        marker=args.profile.parent/('iic_C1_'+profile['bin_sha256'][:16]+'_'+boot+'_'+action+'.json')
        with marker.open('x') as f:json.dump({'boot_id':boot,'profile_sha256':sha(args.profile),'action':action,
                     'prior_result_sha256':sha(args.prior_inventory_result) if args.scan_missing_branches else None},f)
        result.update(boot_id=boot,profile=profile,profile_sha256=sha(args.profile),attempt_marker=str(marker))
        trace('METADATA_GATE_PASS',node=node,profile=profile)
        fd=os.open(node,os.O_RDWR|os.O_SYNC)
        memory=mmap.mmap(fd,4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE)
        io=Native32(memory,Path(__file__).with_name('gpio_mmio32.aarch64.so'))
        pins=GuardPins(io,int(profile['gpio_build_id'],0),trace)
        result['hardware_action_taken']=True;pins.open()
        inventory=Inventory(BitBang(pins,trace),trace)
        rows=inventory.run(args.initialize_full_off)
        if args.scan_missing_branches:
            result['prior_inventory_result']=str(args.prior_inventory_result)
            result['prior_inventory_sha256']=sha(args.prior_inventory_result)
            result['missing_branch_scan']=inventory.scan_missing(rows,prior_channels)
        result['devices']=rows;result['terminal']=pins.close()
        ok=all(r['status']=='REGISTER_READBACK_COMPLETE' for r in rows)
        result.update(result='PASS_DIGITAL_REGISTERS_ONLY' if ok else 'FAIL_PARTIAL_DEVICES',
                      all16_readback_complete=ok,
                      all16_initialized_verified=bool(args.initialize_full_off and ok and all(r['initialization_readback_matches'] for r in rows)))
    except BaseException as e:
        # A protocol/electrical failure is not automatically followed by more
        # hardware I/O. The bounded PL lease releases pads independently of PS.
        result.update(result='FAIL' if result['hardware_action_taken'] else 'BLOCKED',error=repr(e))
        trace('STOP_NO_RETRY_NO_FURTHER_MMIO',error=repr(e))
    finally:
        if io is not None:io.close()
        if memory is not None:memory.close()
        if fd is not None:os.close(fd)
        (args.out/'result.json').write_text(json.dumps(result,indent=2),encoding='utf8')
        trace('EXIT',result=result['result']);stream.close()
    return 0 if result['result']=='PASS_DIGITAL_REGISTERS_ONLY' else 73 if result['result']=='BLOCKED' else 70

if __name__=='__main__':raise SystemExit(main())
