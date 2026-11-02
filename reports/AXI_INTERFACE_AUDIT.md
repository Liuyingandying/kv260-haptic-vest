# AXI4-Lite Interface Audit -- kv260_haptic_top

## 1. Port structure
One AXI4-Lite **slave** port (wired to the KV260 BD `M_AXI`):
```
AW: s_axi_awaddr[15:0], awvalid/awready
W : s_axi_wdata[31:0], wstrb[3:0], wvalid/wready
B : bresp[1:0], bvalid/bready
AR: s_axi_araddr[15:0], arvalid/arready
R : rdata[31:0], rresp[1:0], rvalid/rready
```
32-bit data, 16-bit address -> the KV260 BD must map a **64 KiB** window.

## 2. Address decode (in `rtl/kv260_haptic_top.v`)
```verilog
wire sel_ctrl = (s_axi_awaddr[13:12] == 2'b00) | (s_axi_araddr[13:12] == 2'b00);
wire sel_sts  = (s_axi_awaddr[13:12] == 2'b01) | (s_axi_araddr[13:12] == 2'b01);
wire sel_oe   = (s_axi_awaddr[13:12] == 2'b10) | (s_axi_araddr[13:12] == 2'b10);
```
| Region | Address | Target |
|---|---|---|
| `2'b00` | 0x0000-0x0FFF | `matrix_buffer_axi` (ctrl) -- uses 0x000..0x3FF internally |
| `2'b01` | 0x1000-0x1FFF | `axi_status_regs` -- uses 0x000..0x3FF internally |
| `2'b10` | 0x2000-0x2FFF | OE control register (this file) |
| `2'b11` | 0x3000-0x3FFF | **unmapped** -- see finding F1 |

## 3. Address conflict analysis
| Check | Result |
|---|---|
| Overlap between ctrl (10-bit) and sts (10-bit) | **None** -- they are separated by addr bit 12 before being passed a 10-bit sub-address |
| Overlap ctrl/sts vs OE reg | **None** -- bit 13 separates them |
| Address aliasing within ctrl region | 0x000-0x3FF used; 0x400-0xFFF alias to the same 10-bit space. **Harmless** (PS must simply not write there) but documented |
| **F1: unmapped region 0x3000-0x3FFF** | `sel_ctrl/sel_sts/sel_oe` are all false, so `awready/wready/arready` are driven **0** -> a PS access there would **hang** until timeout |

### F1 assessment
`assign s_axi_awready = sel_ctrl ? ctrl_awready : (sel_sts ? sts_awready : (sel_oe ? 1'b1 : 1'b0));`
An access to 0x3000-0x3FFF returns **no ready** and therefore stalls the AXI
transaction. This is a **robustness gap, not a functional bug** for the intended
PS software (which only uses 0x0000/0x1000/0x2000). Recommended minimal fix (NOT
applied, RTL is frozen for this phase): decode 2'b11 to a dummy 1-cycle responder
returning `SLVERR`. Reported, not modified.

## 4. AXI protocol behaviour from synthesis
- No combinatorial loops reported (`checking loops (0)`)
- No latch inferred in the decode muxes (`Register as Latch = 0`)
- `s_axi_sts_wstrb[3:0]` reported as having no load -- expected: the status block is
  read-only, so its write strobe is legitimately unused.
- `s_axi_awaddr[15:14]` / `[11:10]` (and the same for araddr) reported as unconnected:
  **expected**, bits above 13 are not decoded.

## 5. Clock / reset connection to PS
| Signal | Source | Note |
|---|---|---|
| `pl_clk0` | KV260 BD `psu_pl_clk0` = **96.968727 MHz** | matches the constraint (10.312 ns) |
| `pl_resetn0` | KV260 BD `pl_resetn0` (active low) | synchronised inside `haptic_oe_guard` |
| I2C divider | `CLK_FREQ_HZ / (4*I2C_FREQ_HZ)` = 96,968,727/1,600,000 = **60** -> SCL **404.04 kHz** | within Fast-mode tolerance |

## 6. Not verified
- No BD integration was performed (no `kv260_ps_bd` edit -- KV260 project is frozen).
- No AXI interconnect/SmartConnect mapping was created.
- No PS software was written or run.
