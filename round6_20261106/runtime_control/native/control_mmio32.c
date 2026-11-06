/* Native32 ONLY. No open/mmap/syscall/constructors/runtime/hardware on load.
 * All reads use one volatile u32 LDR and separate ordinary-RAM output.
 */
typedef __UINT32_TYPE__ u32;
typedef __UINTPTR_TYPE__ uptr;
_Static_assert(sizeof(u32)==4,"u32");
#define API __attribute__((visibility("default")))
static int output_ok(volatile void *base,u32 *out) {
    return base && out && !((uptr)base&3u) && !((uptr)out&3u) &&
        !((uptr)out>=(uptr)base && (uptr)out-(uptr)base<0x10000u);
}
static int aligned(u32 off) { return !(off&3u); }
static u32 read_one(volatile void *base,u32 off) {
    volatile const u32 *reg=(volatile const u32*)((volatile char*)base+off);
    __asm__ __volatile__("dmb sy" ::: "memory");
    u32 value=*reg;
    __asm__ __volatile__("dmb sy" ::: "memory");
    return value;
}
API int control_read32(volatile void *base,u32 off,u32 *out) {
    int regs=(off==4u||off==8u||off==12u||off==16u||off==20u||off==24u||off==32u||off==40u);
    if(!output_ok(base,out)||!aligned(off)||!(regs||(off>=0x400u&&off<=0x57cu)||(off>=0xe00u&&off<=0xefcu)))return -1;
    *out=read_one(base,off);return 0;
}
API int control_write32(volatile void *base,u32 off,u32 value) {
    if(!base||((uptr)base&3u)||!aligned(off)||!((off==0u&&value>=1u&&value<=3u)||(off>=0x100u&&off<=0x27cu)))return -1;
    volatile u32 *reg=(volatile u32*)((volatile char*)base+off);
    __asm__ __volatile__("dmb sy" ::: "memory");
    *reg=value;
    __asm__ __volatile__("dmb sy" ::: "memory");
    return 0;
}
API int control_gpio_read32(volatile void *base,u32 off,u32 *out) {
    if(!output_ok(base,out)||off!=0u)return -1;
    *out=read_one(base,off);return 0;
}
API int control_status_read32(volatile void *base,u32 off,u32 *out) {
    /* Existing status bank is READ ONLY. No SW_CTRL/SW_FS writes. */
    if(!output_ok(base,out)||!aligned(off)||off>0x7cu)return -1;
    *out=read_one(base,off);return 0;
}
