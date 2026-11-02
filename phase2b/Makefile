# Phase 2B cross-compile (host -> KV260 aarch64 Linux, static so no board libs needed)
VITIS ?= E:/vivado/2025.2/Vitis
CROSS ?= $(VITIS)/gnu/aarch64/nt/aarch64-linux/bin/aarch64-linux-gnu-gcc.exe
CFLAGS = -O2 -Wall -Wextra -static -o
TARGET = haptic_axi_smoke

all: $(TARGET)

$(TARGET): haptic_axi_smoke.c haptic_regs.h
	"$(CROSS)" $(CFLAGS) $(TARGET) haptic_axi_smoke.c

clean:
	rm -f $(TARGET)

.PHONY: all clean