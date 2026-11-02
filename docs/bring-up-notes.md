# Bring-up notes

See the top-level README for the project overview. This folder holds focused design notes.

Observed on hardware with the peripheral board connected:

```
INIT_DONE       = 0
ONLINE_MASK     = 0x0000
INIT_ERR_MASK   = 0xFFFF
FAULT_LATCHED   = 1
I2C_ERR_CNT     = increasing (~1450/s)
I2C_TIMEOUT_CNT = 0
RECOVERY_STATUS = 0x44  (BACKOFF)
OE_CTRL         = 0x2   (oe_n = 1, outputs disabled - safe)
```

`I2C_TIMEOUT_CNT = 0` with a growing error count means the I2C engine runs normally and no slave
is holding the bus, but nothing is acknowledging. This points at the physical layer.

Next measurement (multimeter, no scope available): connector continuity to the pad pins,
peripheral supply voltage, common ground, and pull-up resistance.
