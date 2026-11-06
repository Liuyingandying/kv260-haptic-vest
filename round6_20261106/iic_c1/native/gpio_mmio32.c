/* No device open/map, syscalls, C runtime, retries or hidden initialization.
 * Caller supplies the verified SIG2 GPIO UIO map. Only GPIO offsets allowed.
 * A stopped AXI transaction cannot be cancelled by this helper or its caller.
 */
typedef __UINT32_TYPE__ u32;
typedef __UINTPTR_TYPE__ uptr;
_Static_assert(sizeof(u32) == 4, "32-bit type required");

int sig2_read32(volatile void *base, u32 offset, u32 *out)
{
    if (!base || !out || ((uptr)base & 3u) ||
        !(offset == 0 || offset == 4 || offset == 8 || offset == 12)) return -1;
    volatile const u32 *reg = (volatile const u32 *)((volatile char *)base + offset);
    __asm__ __volatile__("dmb sy" ::: "memory");
    u32 value = *reg;
    __asm__ __volatile__("dmb sy" ::: "memory");
    *out = value;
    return 0;
}

int sig2_write32(volatile void *base, u32 offset, u32 value)
{
    if (!base || ((uptr)base & 3u) || !(offset == 8 || offset == 12)) return -1;
    volatile u32 *reg = (volatile u32 *)((volatile char *)base + offset);
    __asm__ __volatile__("dmb sy" ::: "memory");
    *reg = value;
    __asm__ __volatile__("dmb sy" ::: "memory");
    return 0;
}
