# Hardware interface

See the top-level README for the project overview. This folder holds focused design notes.

- SCL / SDA are open-drain I2C, LVCMOS33, driven by explicit IOBUF primitives
  (`.I(1'b0)`, `.T(~drv)`, `.O(sense)`).
- No pin is ever driven high by the FPGA; releases rely on pull-ups.
- Current constraints assign SCL to package pin `H12` and SDA to `B10` with on-die pull-ups.

## Unconfirmed items

- The mapping from carrier connector pin numbers to these package pins has **not** been verified
  with a continuity measurement. The constraint file is derived from the vendor BIST reference
  (HDA11 = H12, HDA15 = B10), which uses SOM240 net names, not `J2` pin numbers.
- The peripheral board supply voltage and common-ground path have not been measured.
