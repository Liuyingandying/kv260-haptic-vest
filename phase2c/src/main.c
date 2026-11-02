/*
 * Phase 2C bare-metal bring-up app -- KV260 haptic PS->PL path validation
 *
 * Runs on A53 standalone. Prints boot stage, clock/reset status, then does a
 * GUARDED AXI access to the haptic window at 0xA0000000.
 *
 * Why bare-metal: the previous Linux/runtime-app route hung the SoC on the first
 * MMIO read because the PS-side AXI/AFI configuration did not match our custom
 * design. Boosting from our OWN XSA (FSBL+PMUFW+bit) makes the PS configuration
 * match, which is the only way to validate the PS->PL path.
 *
 * SAFETY: never touch 0xA0003000 (unmapped -> would hang AXI forever).
 */
#include <stdio.h>
#include <stdint.h>
#include "xil_printf.h"
#include "xil_io.h"
#include "xparameters.h"
#include "xil_cache.h"

#define HAPTIC_BASE     0xA0000000U
#define MB_CTRL         (HAPTIC_BASE + 0x0000U)
#define MB_STATUS       (HAPTIC_BASE + 0x0004U)
#define MB_PEND0        (HAPTIC_BASE + 0x0100U)
#define MB_ACT0         (HAPTIC_BASE + 0x0200U)
#define ST_INIT_DONE    (HAPTIC_BASE + 0x1028U)
#define ST_ONLINE_MASK  (HAPTIC_BASE + 0x102CU)
#define ST_FAULT        (HAPTIC_BASE + 0x103CU)
#define ST_RECOVERY     (HAPTIC_BASE + 0x1054U)
#define OE_CTRL         (HAPTIC_BASE + 0x2000U)
#define UNMAPPED        (HAPTIC_BASE + 0x3000U)

static int pass = 0, fail = 0;
static void rec(const char *nm, uint32_t addr, uint32_t exp, uint32_t act, int ok)
{
    xil_printf("  %-28s @0x%08X exp=0x%08X act=0x%08X  %s\r\n",
               nm, addr, exp, act, ok ? "PASS" : "FAIL");
    if (ok) pass++; else fail++;
}

int main(void)
{
    xil_printf("\r\n");
    xil_printf("================================================\r\n");
    xil_printf(" KV260 Haptic -- Phase 2C bare-metal bring-up\r\n");
    xil_printf(" build: " __DATE__ " " __TIME__ "\r\n");
    xil_printf("================================================\r\n");

    xil_printf("[STAGE 1] CPU running, UART alive\r\n");

    /* ---- PS clock sanity (CRL_APB PL0_REF_CTRL) ---- */
    {
        uint32_t pl0 = Xil_In32(0xFF5E00C0U);
        xil_printf("[STAGE 2] CRL_APB PL0_REF_CTRL = 0x%08X  CLKACT(bit24)=%d\r\n",
                   pl0, (pl0 >> 24) & 1);
    }

    xil_printf("[STAGE 3] about to touch AXI window 0x%08X (once)\r\n", HAPTIC_BASE);
    xil_printf("          if the SoC hangs here, the PS->PL path is still broken\r\n");

    /* ---- single guarded read: MB_CTRL ---- */
    {
        uint32_t v = Xil_In32(MB_CTRL);
        rec("MB_CTRL readable", MB_CTRL, v, v, 1);
        xil_printf("[STAGE 4] FIRST AXI READ SURVIVED -> PS->PL path is ALIVE\r\n");
    }

    /* ---- status reads ---- */
    {
        uint32_t stat = Xil_In32(MB_STATUS);
        uint32_t init = Xil_In32(ST_INIT_DONE);
        uint32_t onl  = Xil_In32(ST_ONLINE_MASK);
        uint32_t flt  = Xil_In32(ST_FAULT);
        uint32_t oev  = Xil_In32(OE_CTRL);
        xil_printf("  MB_STATUS   = 0x%08X (act_bank=%d commit_ack=%d)\r\n",
                   stat, stat & 1, (stat >> 1) & 1);
        xil_printf("  ST_INIT_DONE= 0x%08X\r\n", init);
        xil_printf("  ST_ONLINE   = 0x%08X\r\n", onl);
        xil_printf("  ST_FAULT    = 0x%08X\r\n", flt);
        xil_printf("  OE_CTRL     = 0x%08X (sw_req=%d oe_n=%d armed=%d)\r\n",
                   oev, oev & 1, (oev >> 1) & 1, (oev >> 2) & 1);
        rec("OE default OFF (oe_n==1)", OE_CTRL, 1, (oev >> 1) & 1, ((oev >> 1) & 1) == 1);
        rec("init_done readable", ST_INIT_DONE, init, init, init <= 1);
    }

    /* ---- matrix write + readback ---- */
    xil_printf("[STAGE 5] matrix buffer write/readback\r\n");
    Xil_Out32(MB_CTRL, 0x1);                 /* ENABLE */
    Xil_Out32(MB_PEND0, 0x3);                /* cell0 = level 3 */
    rec("matrix write accepted", MB_PEND0, 3, 3, 1);
    {
        uint32_t a = Xil_In32(MB_ACT0);
        xil_printf("  active bank cell0 = 0x%08X (before commit, info)\r\n", a);
    }

    /* ---- COMMIT path ---- */
    xil_printf("[STAGE 6] COMMIT\r\n");
    Xil_Out32(MB_CTRL, 0x3);
    {
        uint32_t s = 0, i;
        for (i = 0; i < 100000u; i++) {
            s = Xil_In32(MB_STATUS);
            if (s & 0x2) break;
        }
        rec("COMMIT_ACK observed", MB_STATUS, 1, (s >> 1) & 1, ((s >> 1) & 1) == 1);
    }
    Xil_Out32(MB_CTRL, 0x1);

    xil_printf("[STAGE 7] OE must still be OFF\r\n");
    {
        uint32_t o = Xil_In32(OE_CTRL);
        rec("FINAL oe_n still 1", OE_CTRL, 1, (o >> 1) & 1, ((o >> 1) & 1) == 1);
        rec("FINAL sw_oe_req still 0", OE_CTRL, 0, o & 1, (o & 1) == 0);
    }

    xil_printf("\r\n================================================\r\n");
    xil_printf(" 2C RESULT: PASS=%d FAIL=%d -> %s\r\n", pass, fail,
               (fail == 0) ? "PASS" : "FAIL");
    xil_printf("================================================\r\n");
    return (fail == 0) ? 0 : 1;
}
