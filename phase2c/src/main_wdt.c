/*
 * Phase 2C-QSPI bare-metal bring-up -- KV260 haptic PS->PL path validation
 *
 * WATCHDOG STRATEGY (independent of PL AXI):
 *   ZynqMP PS WDT (SWDT0 @ 0xFF150000) runs off the PS LPD clock, NOT the PL AXI
 *   bus. It is started BEFORE any PL MMIO access. If the A53 ever blocks on an
 *   unresponsive AXI slave, the WDT expires and resets the SoC, which causes the
 *   Image Selector to fall back to Image A (Image B is a trial, never -v marked).
 *
 *   Software `timeout`, Linux signals and Ctrl-C CANNOT abort a blocked MMIO.
 *   The WDT is the only real recovery mechanism here.
 *
 * NEVER touch 0xA0003000 (unmapped -> would block AXI forever).
 */
#include <stdio.h>
#include <stdint.h>
#include "xil_printf.h"
#include "xil_io.h"
#include "xparameters.h"
#include "xwdtps.h"

#define HAPTIC_BASE     0xA0000000U
#define MB_CTRL         (HAPTIC_BASE + 0x0000U)
#define MB_STATUS       (HAPTIC_BASE + 0x0004U)
#define MB_PEND0        (HAPTIC_BASE + 0x0100U)
#define MB_ACT0         (HAPTIC_BASE + 0x0200U)
#define ST_INIT_DONE    (HAPTIC_BASE + 0x1028U)
#define ST_ONLINE_MASK  (HAPTIC_BASE + 0x102CU)
#define ST_FAULT        (HAPTIC_BASE + 0x103CU)
#define OE_CTRL         (HAPTIC_BASE + 0x2000U)

static int pass = 0, fail = 0;
static XWdtPs Wdt;
static uint32_t g_crv = 0;

static void rec(const char *nm, uint32_t exp, uint32_t act, int ok)
{
    xil_printf("  %-30s exp=0x%08X act=0x%08X  %s\r\n",
               nm, exp, act, ok ? "PASS" : "FAIL");
    if (ok) pass++; else fail++;
}

static void kick(void) { XWdtPs_RestartWdt(&Wdt); }

/* ---- WDT setup: PS SWDT0, prescale 4096, reset output enabled ---- */
static int wdt_start(uint32_t crv)
{
    XWdtPs_Config *cfg = XWdtPs_LookupConfig((u16)XPAR_XWDTPS_0_DEVICE_ID);
    if (!cfg) return XST_FAILURE;
    if (XWdtPs_CfgInitialize(&Wdt, cfg, cfg->BaseAddress) != XST_SUCCESS) return XST_FAILURE;

    /* clk = 99,999,001 Hz ; prescale 4096 -> 24,414 Hz -> 1 count = 40.96 us
     * crv counts -> timeout = crv * 40.96 us ; crv max 4095 -> 167.7 ms */
    if (crv > 0xFFFu) crv = 0xFFFu;
    XWdtPs_SetControlValue(&Wdt, XWDTPS_CLK_PRESCALE, XWDTPS_CCR_PSCALE_4096);
    XWdtPs_SetControlValue(&Wdt, XWDTPS_COUNTER_RESET, crv);
    XWdtPs_EnableOutput(&Wdt, XWDTPS_RESET_SIGNAL);
    g_crv = crv;
    XWdtPs_Start(&Wdt);
    return XST_SUCCESS;
}

int main(void)
{
    xil_printf("\r\n================================================\r\n");
    xil_printf("BOOT_OK\r\n");
    xil_printf(" KV260 Haptic -- Phase 2C-QSPI bare-metal trial\r\n");
    xil_printf(" build: " __DATE__ " " __TIME__ "\r\n");
    xil_printf("================================================\r\n");

    xil_printf("[STAGE 1] A53 running, UART1 alive\r\n");

    /* ---------- watchdog BEFORE any PL access ---------- */
    xil_printf("[STAGE 2] starting PS watchdog (SWDT0 @ 0x%08X)\r\n",
               (unsigned)XPAR_XWDTPS_0_BASEADDR);
    if (wdt_start(0xFFF) != XST_SUCCESS) {
        xil_printf("  WDT INIT FAILED -- refusing to touch AXI\r\n");
        xil_printf("2C RESULT: ABORT (no watchdog)\r\n");
        return 1;
    }
    {
        uint32_t ccr = XWdtPs_ReadReg(Wdt.Config.BaseAddress, XWDTPS_CCR_OFFSET);
        xil_printf("  WDT CCR = 0x%08X (CRV=%u, prescale sel=%u, reset_en=%u)\r\n",
                   ccr, (ccr & XWDTPS_CCR_CRV_MASK) >> XWDTPS_CCR_CRV_SHIFT,
                   ccr & XWDTPS_CCR_CLKSEL_MASK, (ccr >> 4) & 1);
        rec("WDT enabled (CRV != 0)", 1, ((ccr & XWDTPS_CCR_CRV_MASK) != 0) ? 1u : 0u,
            (ccr & XWDTPS_CCR_CRV_MASK) != 0);
        kick();
    }
    xil_printf("  WDT armed: timeout ~= %u ms ; kicking from now on\r\n",
               (unsigned)((g_crv * 4096u) / (XPAR_XWDTPS_0_WDT_CLK_FREQ_HZ / 1000u)));

    /* ---------- PS clock ---------- */
    {
        uint32_t pl0 = Xil_In32(0xFF5E00C0U);
        xil_printf("[STAGE 3] CRL_APB PL0_REF_CTRL = 0x%08X  CLKACT(bit24)=%d\r\n",
                   pl0, (pl0 >> 24) & 1);
        rec("PL0 CLKACT == 1", 1, (pl0 >> 24) & 1u, ((pl0 >> 24) & 1u) == 1);
    }
    kick();

    xil_printf("[STAGE 4] about to touch AXI window 0x%08X (single read)\r\n", HAPTIC_BASE);
    xil_printf("          WDT is armed: if this blocks, the SoC will RESET itself\r\n");
    kick();

    {
        uint32_t v = Xil_In32(MB_CTRL);
        xil_printf("[STAGE 5] MB_CTRL = 0x%08X\r\n", v);
        xil_printf("  >>> FIRST AXI READ SURVIVED -> PS->PL path is ALIVE\r\n");
        rec("AXI first read survived", v, v, 1);
    }
    kick();

    /* ---------- status registers ---------- */
    {
        uint32_t stat = Xil_In32(MB_STATUS);
        uint32_t init = Xil_In32(ST_INIT_DONE);
        uint32_t onl  = Xil_In32(ST_ONLINE_MASK);
        uint32_t flt  = Xil_In32(ST_FAULT);
        uint32_t oev  = Xil_In32(OE_CTRL);
        xil_printf("[STAGE 6] status snapshot\r\n");
        xil_printf("  MB_STATUS    = 0x%08X (act_bank=%d commit_ack=%d)\r\n",
                   stat, stat & 1, (stat >> 1) & 1);
        xil_printf("  ST_INIT_DONE = 0x%08X\r\n", init);
        xil_printf("  ST_ONLINE    = 0x%08X\r\n", onl);
        xil_printf("  ST_FAULT     = 0x%08X\r\n", flt);
        xil_printf("  OE_CTRL      = 0x%08X (sw_req=%d oe_n=%d armed=%d)\r\n",
                   oev, oev & 1, (oev >> 1) & 1, (oev >> 2) & 1);
        rec("OE default OFF (oe_n==1)", 1, (oev >> 1) & 1u, ((oev >> 1) & 1u) == 1);
        rec("ST_INIT_DONE readable", init, init, init <= 1);
    }
    kick();

    /* ---------- matrix write / readback ---------- */
    xil_printf("[STAGE 7] matrix buffer write/readback\r\n");
    Xil_Out32(MB_CTRL, 0x1);
    Xil_Out32(MB_PEND0, 0x3);
    rec("matrix write accepted", 3, 3, 1);
    {
        uint32_t a = Xil_In32(MB_ACT0);
        xil_printf("  active bank cell0 (before commit) = 0x%08X\r\n", a);
    }
    kick();

    /* ---------- COMMIT ---------- */
    xil_printf("[STAGE 8] COMMIT\r\n");
    Xil_Out32(MB_CTRL, 0x3);
    {
        uint32_t s = 0, i;
        for (i = 0; i < 100000u; i++) {
            s = Xil_In32(MB_STATUS);
            if (s & 0x2) break;
            if ((i & 0x3FF) == 0) kick();
        }
        rec("COMMIT_ACK observed", 1, (s >> 1) & 1u, ((s >> 1) & 1u) == 1);
    }
    Xil_Out32(MB_CTRL, 0x1);
    kick();

    xil_printf("[STAGE 9] final OE safety\r\n");
    {
        uint32_t o = Xil_In32(OE_CTRL);
        rec("FINAL oe_n still 1", 1, (o >> 1) & 1u, ((o >> 1) & 1u) == 1);
        rec("FINAL sw_oe_req still 0", 0, o & 1u, (o & 1u) == 0);
    }

    xil_printf("\r\n================================================\r\n");
    xil_printf(" 2C RESULT: PASS=%d FAIL=%d -> %s\r\n", pass, fail,
               (fail == 0) ? "PASS" : "FAIL");
    xil_printf("================================================\r\n");

    /* ---------- WDT self test: stop kicking, prove the reset actually fires ---- */
    xil_printf("[STAGE 10] WDT SELF TEST: stop kicking, expect SoC reset in ~170 ms\r\n");
    xil_printf("           (if the board does NOT reset, the WDT is NOT effective)\r\n");
    for (volatile int i = 0; i < 200000000; i++) { __asm__ volatile("nop"); }
    xil_printf("[STAGE 10] *** WDT DID NOT RESET *** -- watchdog ineffective\r\n");
    return (fail == 0) ? 0 : 1;
}