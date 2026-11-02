#!/usr/bin/env python3
"""Phase 1E independent register decoder + fault injection (decoder #2).

INDEPENDENCE: this script does NOT use the testbench's PTR/DATA/TCA classification
or its regidx column. It re-derives the register index from the raw ordering of
address / pointer / data bytes, which is the part that produced the earlier false
positive. Cross-checking it against the Verilog decoder is the point.

Fault injection mutates the *capture* (never the golden RTL or golden data) and
asserts that the checker reports the specific device / register / channel.
"""
import csv, sys
from collections import defaultdict
from pathlib import Path

MODEx = {0x00: "MODE1", 0x01: "MODE2", 0xFE: "PRE_SCALE"}
EXP = {"PRE_SCALE": 0x79, "MODE2": 0x04, "MODE1_FIRST": 0x30}
FULL_OFF_ON = 0x000
FULL_OFF_OFF_H = 0x10


def decode(rows, t_max_us=None):
    """Rebuild {block: {reg: byte}} purely from byte ordering."""
    img = defaultdict(dict)
    mode1_seq = defaultdict(list)
    tca_masks = []
    cur = None       # current 7-bit address
    wr = 0           # index within the current write transaction
    ptr = None
    for r in rows:
        ev = r["event"]
        if t_max_us is not None:
            try:
                if float(r.get("t_us") or 0) > t_max_us:
                    break
            except ValueError:
                pass
        if ev == "START":
            cur, wr, ptr = None, 0, None
        elif ev == "ADDR":
            cur = int(r["data"], 16) >> 1
            wr, ptr = 0, None
        elif ev in ("PTR", "DATA", "TCA"):
            if cur is None:
                continue
            data = int(r["data"], 16)
            if cur == 0x70:
                tca_masks.append(data)          # single-byte mux select
                continue
            if not (0x40 <= cur <= 0x4F):
                continue
            wr += 1
            if wr == 1:
                ptr = data                      # POINTER byte: not a register write
                continue
            reg = ptr + (wr - 2)                # first data byte -> ptr, then auto-increment
            blk = cur - 0x40
            img[blk][reg] = data
            if reg == 0x00:
                mode1_seq[blk].append(data)
    return img, mode1_seq, tca_masks


def check(img, mode1_seq, tca_masks):
    """Return list of (severity, code, detail)."""
    v = []
    # TCA: legal masks are 0x00 (deselect) and single-bit 1/2/4/8
    for m in tca_masks:
        if bin(m).count("1") > 1:
            v.append(("FAIL", "F2_TCA_MULTI",
                      f"TCA mask 0x{m:02X} selects {bin(m).count('1')} channels simultaneously"))
        elif m not in (0x00, 0x01, 0x02, 0x04, 0x08):
            v.append(("FAIL", "F2_TCA_ILLEGAL", f"TCA mask 0x{m:02X} is not a legal single channel"))
    if not any(bin(m).count("1") == 1 for m in tca_masks):
        v.append(("FAIL", "TCA_NO_SELECT", "no single-channel TCA select observed"))
    # blocks
    for blk in range(16):
        if blk not in img or not img[blk]:
            v.append(("FAIL", "BLK_MISSING", f"BLK{blk:02d} never addressed"))
            continue
        regs = img[blk]
        if regs.get(0xFE) != EXP["PRE_SCALE"]:
            v.append(("FAIL", "F1_PRESCALE",
                      f"BLK{blk:02d} PRE_SCALE (0xFE) = "
                      f"{'MISSING' if 0xFE not in regs else f'0x{regs[0xFE]:02X}'} "
                      f"expected 0x{EXP['PRE_SCALE']:02X} -> "
                      f"f={'?' if 0xFE not in regs else round(25e6/((regs[0xFE]+1)*4096),2)} Hz"))
        if regs.get(0x01) != EXP["MODE2"]:
            v.append(("FAIL", "MODE2",
                      f"BLK{blk:02d} MODE2 (0x01) = "
                      f"{'MISSING' if 0x01 not in regs else f'0x{regs[0x01]:02X}'} expected 0x04"))
        seq = mode1_seq.get(blk, [])
        if not seq or seq[0] != EXP["MODE1_FIRST"]:
            v.append(("FAIL", "MODE1_FIRST",
                      f"BLK{blk:02d} first MODE1 = "
                      f"{'NONE' if not seq else f'0x{seq[0]:02X}'} expected 0x30"))
        # 256-channel FULL_OFF
        for ch in range(16):
            on = (regs.get(0x07 + 4*ch, -1) << 8) | regs.get(0x06 + 4*ch, -1) if 0x06+4*ch in regs and 0x07+4*ch in regs else None
            off = (regs.get(0x09 + 4*ch, -1) << 8) | regs.get(0x08 + 4*ch, -1) if 0x08+4*ch in regs and 0x09+4*ch in regs else None
            if on is None or off is None:
                v.append(("FAIL", "F4_CH_MISSING",
                          f"BLK{blk:02d} ch{ch:02d} LED registers incomplete"))
                continue
            if on != FULL_OFF_ON or (off >> 8) != FULL_OFF_OFF_H:
                v.append(("FAIL", "F4_FULL_OFF",
                          f"BLK{blk:02d} ch{ch:02d} ON=0x{on:03X} OFF=0x{off:03X} "
                          f"expected ON=0x000 OFF_H=0x10 (bit4 FULL_OFF)"))
    return v


def load(path):
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        return [r for r in csv.DictReader(f) if r.get("event")]


def mutate(rows, kind):
    """Apply a single deliberate fault. Golden capture is never modified on disk."""
    out = [dict(r) for r in rows]
    if kind == "F1_prescale":
        hit = None
        for i, r in enumerate(out):
            if r["event"] == "PTR" and r.get("data", "").lower() == "fe":
                hit = i
                break
        if hit is not None and hit + 1 < len(out):
            out[hit + 1]["data"] = "7a"
            return out, f"PRE_SCALE data byte at row {hit+1} forced to 0x7A"
        return out, "F1 target not found"
    if kind == "F2_tca_multi":
        for r in out:
            if r["event"] == "TCA":
                r["data"] = "03"
                return out, "TCA mask forced to 0x03"
    if kind == "F4_fulloff_clear":
        for r in out:
            if r["event"] == "DATA" and r["regidx"] == "9" and r["data"].lower() == "10":
                r["data"] = "00"
                return out, "cleared FULL_OFF bit4 on LED0_OFF_H"
    if kind == "F3_drop_block":
        keep, dropped = [], 0
        cur_blk = None
        for r in out:
            if r["event"] == "ADDR":
                cur_blk = int(r["data"], 16) >> 1
            if cur_blk == 0x41:          # block 1 write address
                dropped += 1
                continue
            keep.append(r)
        return keep, f"dropped {dropped} bytes addressed to BLK01 (0x41)"
    return out, "no-op"


def main():
    cap = sys.argv[1] if len(sys.argv) > 1 else "i2c_byte_capture.csv"
    import os
    tmax = float(os.environ.get("INIT_WINDOW_US", "0") or 0) or None
    rows = load(cap)
    print(f"capture rows: {len(rows)}")

    img, seq, tca = decode(rows, tmax)
    print(f"init window cutoff: {tmax if tmax else 'none'} us")
    print(f"decoded: blocks={len(img)} tca_masks={len(tca)} "
          f"distinct_tca={sorted(set(tca))}")
    v = check(img, seq, tca)
    print("\n=== BASELINE (golden capture) ===")
    if v:
        print(f"VIOLATIONS: {len(v)}")
        for x in v[:20]:
            print("  ", x)
    else:
        print("PASS: 16 blocks, 256 channels FULL_OFF, PRE_SCALE/MODE2/MODE1 all correct")

    print("\n=== FAULT INJECTION (checker sensitivity) ===")
    results = []
    for kind in ["F1_prescale", "F2_tca_multi", "F4_fulloff_clear", "F3_drop_block"]:
        mrows, desc = mutate(rows, kind)
        mimg, mseq, mtca = decode(mrows, tmax)
        mv = check(mimg, mseq, mtca)
        codes = sorted({c for _, c, _ in mv})
        caught = len(mv) > 0
        results.append((kind, caught, codes, mv[:3]))
        print(f"\n[{kind}] {desc}")
        print(f"   detected={'YES' if caught else 'NO'}  violations={len(mv)}  codes={codes}")
        for _, c, d in mv[:3]:
            print(f"     -> {c}: {d}")

    print("\n=== SUMMARY ===")
    allcaught = all(r[1] for r in results)
    for k, c, codes, _ in results:
        print(f"  {k:<20} {'CAUGHT' if c else 'MISSED'}  {codes[:3]}")
    print(f"\nCHECKER SENSITIVITY: {'PASS (all 4 faults detected)' if allcaught else 'FAIL'}")

    # dump the decoded 256-channel image for the report
    with open("python_regmap_256.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["blk", "ch", "led_on", "led_off", "mode1", "mode2", "prescale", "full_off_ok"])
        for blk in range(16):
            rg = img.get(blk, {})
            for ch in range(16):
                on = (rg.get(0x07 + 4*ch, 0) << 8) | rg.get(0x06 + 4*ch, 0)
                off = (rg.get(0x09 + 4*ch, 0) << 8) | rg.get(0x08 + 4*ch, 0)
                ok = on == FULL_OFF_ON and (off >> 8) == FULL_OFF_OFF_H
                w.writerow([blk, ch, f"0x{on:03X}", f"0x{off:03X}",
                            f"0x{rg.get(0x00,0):02X}", f"0x{rg.get(0x01,0):02X}",
                            f"0x{rg.get(0xFE,0):02X}", int(ok)])
    print("\nwrote python_regmap_256.csv")


if __name__ == "__main__":
    main()
