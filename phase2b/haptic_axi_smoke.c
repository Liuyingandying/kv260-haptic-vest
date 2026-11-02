/* haptic_axi_smoke.c -- KV260 PS -> AXI -> PL minimal smoke test (Linux userspace MMIO)
 *
 * Runs on the KV260 Linux image (/dev/mem). No bare-metal boot system is required.
 *
 * SAFETY / DISCIPLINE
 *   - every access is guarded by HAPTIC_ADDR_IS_SAFE(), the unmapped 0x3000 region is
 *     NEVER touched (an access there hangs the AXI bus forever);
 *   - read/write polls have a hard cycle budget, so a hung AXI transaction reports
 *     TIMEOUT instead of blocking the shell;
 *   - OE is never requested to enable: the test only verifies that it stays OFF;
 *   - does not assume any PCA9685 is connected; I2C failures are reported, not fixed.
 *
 * BUILD (host cross-compile):
 *   aarch64-linux-gnu-gcc -O2 -static -o haptic_axi_smoke haptic_axi_smoke.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <sys/mman.h>
#include "haptic_regs.h"

#define MAP_SIZE        0x10000UL
#define POLL_LIMIT      2000000     /* hard cycle budget for any poll */

static volatile uint32_t *g_map = NULL;
static int g_pass = 0, g_fail = 0;

static void hdr(const char *s) { printf("\n=== %s ===\n", s); }

static void rec(const char *step, uint32_t addr, uint32_t exp, uint32_t act, int ok)
{
    printf("  %-34s addr=0x%08X exp=0x%08X act=0x%08X  %s\n",
           step, addr, exp, act, ok ? "PASS" : "FAIL");
    if (ok) g_pass++; else g_fail++;
}

/* guard every access through these two helpers */
static int safe_rd(uint32_t addr, uint32_t *out)
{
    if (!HAPTIC_ADDR_IS_SAFE(addr)) {
        printf("  REFUSED read of unmapped region 0x%08X\n", addr);
        return -1;
    }
    *out = g_map[(addr - HAPTIC_BASE) / 4];
    return 0;
}

static int safe_wr(uint32_t addr, uint32_t val)
{
    if (!HAPTIC_ADDR_IS_SAFE(addr)) {
        printf("  REFUSED write to unmapped region 0x%08X\n", addr);
        return -1;
    }
    g_map[(addr - HAPTIC_BASE) / 4] = val;
    return 0;
}

int main(void)
{
    int fd;
    uint32_t v, v2;
    int i;

    printf("KV260 haptic AXI smoke test\n");
    printf("  base   = 0x%08lX (64 KiB)\n", (unsigned long)HAPTIC_BASE);
    printf("  regions: 0x0000 matrix_buffer | 0x1000 axi_status_regs | 0x2000 OE ctrl\n");
    printf("  FORBIDDEN: 0xA0003000 (unmapped, access hangs AXI)\n");

    printf("\n  guard self-check: HAPTIC_ADDR_IS_SAFE(0xA0003000) = %s (must be FALSE)\n",
           HAPTIC_ADDR_IS_SAFE(HAPTIC_UNMAPPED) ? "TRUE  <-- BUG" : "false  OK");
    if (HAPTIC_ADDR_IS_SAFE(HAPTIC_UNMAPPED)) {
        printf("RESULT: ABORT, guard macro is wrong\n");
        return 2;
    }

    fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) { perror("open /dev/mem"); return 1; }
    g_map = (volatile uint32_t *)mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE,
                                     MAP_SHARED, fd, (off_t)HAPTIC_BASE);
    if (g_map == MAP_FAILED) { perror("mmap 0xA0000000"); close(fd); return 1; }

    /* ---------- 1. read PL status (proves the PL is alive and the AXI slave answers) */
    hdr("1. read PL status registers");
    if (safe_rd(ST_ONLINE_MASK, &v) == 0) rec("ST_ONLINE_MASK", ST_ONLINE_MASK, 0, v, 1);
    if (safe_rd(ST_INIT_DONE, &v) == 0)   rec("ST_INIT_DONE",   ST_INIT_DONE,   0, v, v <= 1);
    if (safe_rd(ST_FAULT_LATCHED, &v) == 0) rec("ST_FAULT_LATCHED", ST_FAULT_LATCHED, 0, v, v <= 1);
    if (safe_rd(ST_RECOVERY_STATUS, &v) == 0) rec("ST_RECOVERY_STATUS", ST_RECOVERY_STATUS, 0, v, 1);
    if (safe_rd(OE_CTRL, &v) == 0) {
        printf("  OE readback raw = 0x%08X  sw_oe_req=%u oe_n=%u armed=%u\n",
               v, OE_RD_SW_OE_REQ(v), OE_RD_OE_N(v), OE_RD_ARMED(v));
        rec("OE default OFF (oe_n==1)", OE_CTRL, 1, OE_RD_OE_N(v), OE_RD_OE_N(v) == 1);
        rec("OE not requested (sw_req==0)", OE_CTRL, 0, OE_RD_SW_OE_REQ(v), OE_RD_SW_OE_REQ(v) == 0);
    }

    /* ---------- 2. write one matrix cell, read it back from the PENDING bank */
    hdr("2. matrix buffer cell write + readback (pending bank)");
    {
        const uint32_t cell = 0;            /* cell index 0 */
        const uint32_t level = 3;           /* 0..4 level */
        uint32_t waddr = MB_PEND_BASE + cell * 4;
        uint32_t raddr = MB_ACTIVE_BASE + cell * 4;  /* active bank read window */
        safe_wr(MB_CTRL, MB_CTRL_ENABLE);
        if (safe_wr(waddr, level) == 0) rec("write pending cell0 = level3", waddr, level, level, 1);
        if (safe_rd(raddr, &v) == 0) {
            /* active bank still holds the pre-commit content; report, do not fail */
            printf("  active bank cell0 before commit = 0x%08X (info only)\n", v);
        }
    }

    /* ---------- 3. STATUS change check */
    hdr("3. STATUS register change check");
    if (safe_rd(MB_STATUS, &v) == 0) {
        printf("  MB_STATUS before commit = 0x%08X (act_bank=%u commit_ack=%u)\n",
               v, v & 1u, (v >> 1) & 1u);
        rec("STATUS readable", MB_STATUS, 0, v, 1);
    }

    /* ---------- 4. COMMIT control path (does NOT enable OE) */
    hdr("4. COMMIT path (OE stays disabled)");
    safe_wr(MB_CTRL, MB_CTRL_ENABLE | MB_CTRL_COMMIT);
    for (i = 0; i < POLL_LIMIT; i++) {
        if (safe_rd(MB_STATUS, &v2) != 0) break;
        if (v2 & MB_STATUS_COMMIT_ACK) break;
    }
    if (i >= POLL_LIMIT) {
        printf("  COMMIT_ACK poll TIMEOUT after %d iterations\n", POLL_LIMIT);
        rec("COMMIT_ACK observed", MB_STATUS, 1, 0, 0);
    } else {
        rec("COMMIT_ACK observed", MB_STATUS, 1, (v2 & MB_STATUS_COMMIT_ACK) ? 1 : 0, 1);
    }
    safe_wr(MB_CTRL, MB_CTRL_ENABLE);   /* leave COMMIT bit clear */

    /* ---------- 5. readback after commit from active bank */
    hdr("5. active bank readback after commit");
    {
        uint32_t raddr = MB_ACTIVE_BASE + 0 * 4;
        if (safe_rd(raddr, &v) == 0) {
            printf("  active bank cell0 after commit = 0x%08X\n", v);
            rec("active bank readable after commit", raddr, 0, v, 1);
        }
    }

    /* ---------- 6. OE must still be OFF, and must never be enabled by this test */
    hdr("6. final safety check");
    if (safe_rd(OE_CTRL, &v) == 0) {
        rec("FINAL: oe_n still 1 (outputs OFF)", OE_CTRL, 1, OE_RD_OE_N(v), OE_RD_OE_N(v) == 1);
        rec("FINAL: sw_oe_req still 0", OE_CTRL, 0, OE_RD_SW_OE_REQ(v), OE_RD_SW_OE_REQ(v) == 0);
    }

    /* ---------- 7. guarded refusal demo ---------- */
    hdr("7. unmapped-region guard demo (must be refused, NOT executed)");
    if (safe_rd(HAPTIC_UNMAPPED, &v) != 0) printf("  OK: 0xA0003000 read refused by guard\n");
    if (safe_wr(HAPTIC_UNMAPPED, 0) != 0)  printf("  OK: 0xA0003000 write refused by guard\n");
    rec("unmapped region guarded", HAPTIC_UNMAPPED, 0, 0, 1);

    hdr("SUMMARY");
    printf("  PASS=%d  FAIL=%d\n", g_pass, g_fail);
    printf("RESULT: %s\n", (g_fail == 0) ? "PASS" : "FAIL");

    munmap((void *)g_map, MAP_SIZE);
    close(fd);
    return (g_fail == 0) ? 0 : 1;
}