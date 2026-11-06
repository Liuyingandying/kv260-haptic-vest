# Offline tests

From the repository root:

```sh
python -B round6_20261106/tests/run_tests.py
python -B round6_20261106/tests/run_tests.py --suite rtl
```

Python3.10+ and the standard library are sufficient for the default Host suite. The RTL suite additionally requires Icarus Verilog (`iverilog`, `vvp`) on PATH; alternatively pass `--iverilog <executable> --vvp <executable>`. It checks the original M2 adapter SHA before simulation and uses the published `fpga_c2/rtl` source layout. No Vivado, ARM toolchain, SDK, COM, board, or privileged access is used.

The six Host modules contain57 selected tests, including the existing28 source-recovery/window checks. Synthetic G6C1 fixtures exercise CRC, mapping, UNKNOWN, anti-replay, two identities, error-stop, renewal and formal-gate boundaries. The453-packet replay here is intentionally synthetic; it is not a publication of captured camera observations. An AST-only historical read8 function reference supports the unchanged-cleanup comparison. No old PASS is attributed to these adapted public tests before they run.

The22-case RTL suite is the existing DGM2 adapter/peer-RAM model regression. It is not a physical waveform, PCA identity or motor test. C3-specific stability tests are excluded from this M2 suite. `SOURCE_SELECTION.json` identifies original source hashes and adaptations. Generated results stay under ignored `tests/results/`.

Runtime dependencies are copied by the release owner: `PACKAGE_MANIFEST.json`, `runtime_night/run_d1_live.py`, `runtime_control/`, `runtime_compact/`, and original M2 `fpga_c2/rtl/`. The sealed runtime files are tested without modifications. SDKcleanup remains UNKNOWN and production FRESH remains BLOCKED; tests do not grant physical permission.
