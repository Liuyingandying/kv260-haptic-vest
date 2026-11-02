/* haptic_regs.h -- KV260 haptic AXI register map
 *
 * Derived FROM THE FROZEN RTL SOURCE, not guessed:
 *   rtl/matrix_buffer_axi.v          (offset 0x0000 region)
 *   rtl/axi_status_regs.v            (offset 0x1000 region, read map at araddr[6:2])
 *   rtl/kv260_haptic_top.v           (offset 0x2000 OE control register)
 *
 * Base address from Phase 2A Block Design: 0xA000_0000, 64 KiB aperture.
 * NOTE: 0xA000_3000..0xA000_3FFF is UNMAPPED -> an access HANGS the AXI bus.
 *       The macro HAPTIC_ADDR_IS_SAFE() must be used before any access.
 *
 * FROZEN RTL: do not change register semantics to fit software. Software adapts.
 */
#ifndef HAPTIC_REGS_H
#define HAPTIC_REGS_H

#include <stdint.h>

#define HAPTIC_BASE            0xA0000000UL

/* ---- region selectors (addr[13:12]) ---- */
#define HAPTIC_MATRIX_BASE      (HAPTIC_BASE + 0x0000UL)   /* matrix_buffer_axi */
#define HAPTIC_STATUS_BASE      (HAPTIC_BASE + 0x1000UL)   /* axi_status_regs    */
#define HAPTIC_OE_BASE          (HAPTIC_BASE + 0x2000UL)   /* OE control reg     */
#define HAPTIC_UNMAPPED         (HAPTIC_BASE + 0x3000UL)   /* NEVER ACCESS       */

/* ---- 0x0000 : matrix_buffer_axi ---- */
#define MB_CTRL                 (HAPTIC_MATRIX_BASE + 0x000UL)
#define   MB_CTRL_ENABLE        (1u << 0)   /* RW  */
#define   MB_CTRL_COMMIT        (1u << 1)   /* W1 : trigger frame switch */
#define MB_STATUS               (HAPTIC_MATRIX_BASE + 0x004UL)
#define   MB_STATUS_ACT_BANK    (1u << 0)   /* RO  */
#define   MB_STATUS_COMMIT_ACK  (1u << 1)   /* RO, read-clears */
#define MB_PEND_BASE            (HAPTIC_MATRIX_BASE + 0x100UL)  /* 0x100..0x1FF pending matrix */
#define MB_ACTIVE_BASE          (HAPTIC_MATRIX_BASE + 0x200UL)  /* 0x200..0x2FF active  matrix */

/* ---- 0x1000 : axi_status_regs (byte offset = index*4, index = araddr[6:2]) ---- */
#define ST_FRAME_CNT            (HAPTIC_STATUS_BASE + 0x000UL)  /* idx 0  */
#define ST_BURST_CNT            (HAPTIC_STATUS_BASE + 0x004UL)  /* idx 1  */
#define ST_FLAGS                (HAPTIC_STATUS_BASE + 0x008UL)  /* idx 2  */
#define ST_SW_CTRL              (HAPTIC_STATUS_BASE + 0x00CUL)  /* idx 3  W: bit0 commit, bit1 tca_en, bit2 fault_clear; R: bit1 echo */
#define ST_DIRTY_BITMAP         (HAPTIC_STATUS_BASE + 0x010UL)  /* idx 4  */
#define ST_DIRTY_COUNT          (HAPTIC_STATUS_BASE + 0x014UL)  /* idx 5  */
#define ST_V12_FLAGS            (HAPTIC_STATUS_BASE + 0x018UL)  /* idx 6  */
#define ST_DD_STATUS            (HAPTIC_STATUS_BASE + 0x01CUL)  /* idx 7  */
#define ST_ERR_FLAGS            (HAPTIC_STATUS_BASE + 0x020UL)  /* idx 8  */
#define ST_SW_FS_CTRL           (HAPTIC_STATUS_BASE + 0x024UL)  /* idx 9  W: bit0 sw_force_off */
#define ST_INIT_DONE            (HAPTIC_STATUS_BASE + 0x028UL)  /* idx 10 */
#define ST_ONLINE_MASK          (HAPTIC_STATUS_BASE + 0x02CUL)  /* idx 11 */
#define ST_INIT_ERR_MASK        (HAPTIC_STATUS_BASE + 0x030UL)  /* idx 12 */
#define ST_RUN_ERR_MASK         (HAPTIC_STATUS_BASE + 0x034UL)  /* idx 13 */
#define ST_OFF_PEND_MASK        (HAPTIC_STATUS_BASE + 0x038UL)  /* idx 14 */
#define ST_FAULT_LATCHED        (HAPTIC_STATUS_BASE + 0x03CUL)  /* idx 15 */
#define ST_I2C_ERR_CNT          (HAPTIC_STATUS_BASE + 0x040UL)  /* idx 16 */
#define ST_OFF_RETRY_CNT        (HAPTIC_STATUS_BASE + 0x044UL)  /* idx 17 */
#define ST_ACTIVE_GEN           (HAPTIC_STATUS_BASE + 0x048UL)  /* idx 18 */
#define ST_PENDING_GEN          (HAPTIC_STATUS_BASE + 0x04CUL)  /* idx 19 */
#define ST_I2C_TIMEOUT_CNT      (HAPTIC_STATUS_BASE + 0x050UL)  /* idx 20 */
#define ST_RECOVERY_STATUS      (HAPTIC_STATUS_BASE + 0x054UL)  /* idx 21, bit6 = no HW cutoff */

/* ---- 0x2000 : OE control (kv260_haptic_top.v) ---- */
#define OE_CTRL                 (HAPTIC_OE_BASE + 0x000UL)
#define   OE_CTRL_SW_OE_REQ     (1u << 0)   /* W: 1 = request output enable   */
#define   OE_CTRL_SW_OE_CLR     (1u << 1)   /* W: 1 = force request back to 0 */
/* read back: {28'd0, armed, oe_n, sw_oe_req} in bits [3:0]
 *   bit0 = sw_oe_req, bit1 = oe_n (1 = OE# HIGH = OUTPUTS OFF), bit2 = armed */
#define   OE_RD_SW_OE_REQ(v)    (((v) >> 0) & 1u)
#define   OE_RD_OE_N(v)         (((v) >> 1) & 1u)   /* 1 => outputs DISABLED */
#define   OE_RD_ARMED(v)        (((v) >> 2) & 1u)

/* ---- safety guard: refuse the unmapped 0x3000 region ---- */
#define HAPTIC_ADDR_IS_SAFE(a)  ((((uintptr_t)(a)) & 0xFFFFF000UL) != \
                                 (((uintptr_t)HAPTIC_UNMAPPED) & 0xFFFFF000UL))

#endif /* HAPTIC_REGS_H */