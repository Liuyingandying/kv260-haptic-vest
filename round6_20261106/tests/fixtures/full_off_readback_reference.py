"""AST-only frozen full_off_readback reference; never imported or executed.
Source package: package_20261106T024131320625Z; source SHA256: 516dcd3a465827f85219467dd4e82a3727281dffcaab4b7300d34353a908e6af
"""
def full_off_readback(native,log):
    # Existing low-level engine, retargeted by explicit constructor ID; never main()/run().
    from digital_iic_diag import GuardPins,BitBang,Inventory
    pins=GuardPins(native,DESIGN_ID,lambda stage,**kw:log.event('FULL_OFF_'+stage,**kw))
    rows=[];opened=False;result={'status':'NOT_VERIFIED','devices':rows,'physical_shutdown':'UNVERIFIED'}
    try:
        pins.open();opened=True;bus=BitBang(pins,lambda stage,**kw:log.event('FULL_OFF_'+stage,**kw),session_budget_s=FULL_OFF_READBACK_BUDGET_S);inventory=Inventory(bus,lambda stage,**kw:log.event('FULL_OFF_'+stage,**kw))
        for block in (4,5,6,7,12,13,14,15):
            row=inventory.device(block,initialize=False);rows.append(row)
            require(row['status']=='REGISTER_READBACK_COMPLETE' and row.get('full_off_register_bits') is True,'PCA_FULL_OFF_ACTUAL_READBACK_FAILED')
        # Mux deselection only; never PWM or initialization writes.
        bus.transfer(0x70,[0]);result['status']='VERIFIED_REGISTER_BITS_ONLY'
    finally:
        if (opened or pins.tri!=0xffffffff) and not log.failed and not log.pending and not log.stop.exists():
            try:result['terminal']=pins.close()
            finally:
                # Only disable requests after returned errors; never after unknown MMIO.
                if not log.failed and not log.pending and not log.stop.exists():
                    native.write(8,0);native.write(12,0xffffffff)
                    result['TRI_after']=native.read(12)
                    require(result['TRI_after']==0xffffffff,'DIAGNOSTIC_TRI_NOT_RESTORED')
    return result
