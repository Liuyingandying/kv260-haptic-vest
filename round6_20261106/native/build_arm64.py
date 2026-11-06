"""Build the selected native source variant; never execute the SDK or output ELF.

Only SDK_ROOT, CXX and optionally SYSROOT are environment configuration.
--verify-only checks the shipped source closure without a compiler or SDK.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parent


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_source_closure():
    manifest = json.loads((ROOT / "SOURCE_ORIGIN.json").read_text(encoding="utf8"))
    require(manifest.get("schema") == "R6_NATIVE_SOURCE_CLOSURE_V1", "SOURCE_ORIGIN_SCHEMA")
    for relative, row in manifest["files"].items():
        rel = Path(relative)
        require(not rel.is_absolute() and ".." not in rel.parts, "SOURCE_PATH_ESCAPE")
        source = ROOT / rel
        require(source.is_file() and not source.is_symlink(), "SOURCE_MISSING: " + relative)
        require(source.stat().st_size == row["bytes"] and digest(source) == row["sha256"],
                "FROZEN_SOURCE_CHANGED: " + relative)
        for include in re.findall(r'^\s*#\s*include\s*"([^"\n]+)"', source.read_text(encoding="utf8"), re.M):
            if include.startswith("libobsensor/"):
                continue
            dependency = (source.parent / include).resolve()
            require(dependency.is_relative_to(ROOT) and dependency.is_file(),
                    "LOCAL_INCLUDE_MISSING_OR_EXTERNAL: " + relative + " -> " + include)
    return manifest


def assert_aarch64_elf(path):
    with Path(path).open("rb") as stream:
        header = stream.read(64)
    require(len(header) >= 20 and header[:6] == b"\x7fELF\x02\x01"
            and struct.unpack_from("<H", header, 18)[0] == 183,
            "EXPECTED_LITTLE_ENDIAN_ELF64_AARCH64: " + Path(path).name)


def settings():
    require(bool(os.environ.get("CXX")), "Set CXX to an AArch64/Linux g++ compiler")
    candidate = os.environ["CXX"]
    compiler = str(Path(candidate).resolve()) if Path(candidate).is_file() else shutil.which(candidate)
    require(compiler is not None, "CXX_NOT_FOUND")
    machine = subprocess.run([compiler, "-dumpmachine"], capture_output=True, text=True, check=True).stdout.strip()
    require(machine.lower().startswith("aarch64") and "linux" in machine.lower(),
            "CXX_MUST_TARGET_AARCH64_LINUX: " + machine)
    require(bool(os.environ.get("SDK_ROOT")), "Set SDK_ROOT to the external Orbbec SDK1.10.37 SDK directory")
    sdk = Path(os.environ["SDK_ROOT"]).resolve()
    require((sdk / "include/libobsensor/ObSensor.hpp").is_file(), "SDK_HEADER_MISSING")
    require((sdk / "lib/libOrbbecSDK.so").is_file(), "SDK_LINK_LIBRARY_MISSING")
    assert_aarch64_elf(sdk / "lib/libOrbbecSDK.so")
    require(digest(sdk / "lib/libOrbbecSDK.so") ==
            "18c09b76a0f2237d127ebf9fa131ee2ef0f3ceb5fc06f2639f636e170b6731a1",
            "EXACT_EXTERNAL_ORBBEC_1_10_37_LIBRARY_REQUIRED")
    sysroot = Path(os.environ["SYSROOT"]).resolve() if os.environ.get("SYSROOT") else None
    if sysroot is not None:
        require(sysroot.is_dir(), "SYSROOT_MISSING")
    return compiler, sdk, sysroot, machine


def sysroot_options(sysroot):
    """Use the target Ubuntu C++ headers/libraries, not Windows host headers."""
    if sysroot is None:
        return [], []
    options = ["--sysroot=" + str(sysroot)]
    root = sysroot / "usr/include/c++"
    versions = sorted((p for p in root.glob("*") if p.is_dir() and re.fullmatch(r"\d+(?:\.\d+)*", p.name)),
                      key=lambda p: tuple(int(x) for x in p.name.split(".")))
    require(bool(versions), "SYSROOT_TARGET_CPP_HEADERS_MISSING")
    version = versions[-1].name
    options += ["-nostdinc++"]
    for relative in ("usr/include/c++/" + version,
                     "usr/include/aarch64-linux-gnu/c++/" + version,
                     "usr/include/c++/" + version + "/backward",
                     "usr/include/aarch64-linux-gnu"):
        directory = sysroot / relative
        require(directory.is_dir(), "SYSROOT_UBUNTU_CPP_INCLUDE_MISSING: " + relative)
        options += ["-isystem", str(directory)]
    links = []
    for relative in ("usr/lib/aarch64-linux-gnu", "usr/lib/gcc/aarch64-linux-gnu/" + version):
        directory = sysroot / relative
        require(directory.is_dir(), "SYSROOT_UBUNTU_LIBRARY_DIRECTORY_MISSING: " + relative)
        options += ["-B" + str(directory) + os.sep, "-L" + str(directory)]
        links += ["-Wl,-rpath-link," + str(directory)]
    library = sysroot / "lib/aarch64-linux-gnu"
    if library.is_dir():
        links += ["-Wl,-rpath-link," + str(library)]
    return options, links


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("bounded60", "continuous600"), default="bounded60")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    manifest = verify_source_closure()
    if args.verify_only:
        print(json.dumps({"status": "PASS_SOURCE_CLOSURE_ONLY", "files": len(manifest["files"]),
                          "compiler_run": False, "SDK_started": False, "hardware_action": False}))
        return 0
    compiler, sdk, sysroot, machine = settings()
    target_options, target_links = sysroot_options(sysroot)
    variant = ROOT / args.variant
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / "build" / (args.variant + "_" + stamp)
    out.mkdir(parents=True, exist_ok=False)
    common = [compiler, "-std=c++17", "-O3", "-fno-fast-math", "-ffp-contract=off", "-pthread", *target_options]
    obj = out / "gemini_geometry.o"
    executable = out / ("gemini_live_compact.aarch64" if args.variant == "bounded60"
                        else "gemini_live_compact_continuous.aarch64")
    validator = out / "compact_validator.aarch64.so"
    commands = [
        ("compile_geometry", common + ["-fno-exceptions", "-fno-rtti", "-fPIC", "-c",
                                      str(ROOT / "shared/gemini_geometry.cpp"), "-o", str(obj)]),
        ("compile_capture", common + ["-Wall", "-Wextra", "-Werror", "-I" + str(sdk / "include"),
                                     str(variant / "gemini_live_compact.cpp"), str(obj),
                                     "-L" + str(sdk / "lib"), "-lOrbbecSDK",
                                     "-Wl,-rpath-link," + str(sdk / "lib"), *target_links,
                                     "-Wl,-rpath,$ORIGIN/sdk/lib", "-o", str(executable)]),
        ("compile_validator", common + ["-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                                       str(variant / "compact_observation.cpp"), "-o", str(validator)]),
    ]
    report = {"schema": "R6_PORTABLE_NATIVE_BUILD_V1", "variant": args.variant,
              "status": "FAIL_OR_INCOMPLETE", "target": machine, "steps": [],
              "source_origin_sha256": digest(ROOT / "SOURCE_ORIGIN.json"),
              "historically_accepted_ELF_sha256": manifest["variants"][args.variant]["actual_verified_ELF_sha256"],
              "SDK_started": False, "compiled_program_executed": False, "hardware_action": False,
              "bit_identical_rebuild_claim": False}
    try:
        for name, command in commands:
            completed = subprocess.run(command, cwd=out, capture_output=True, timeout=300)
            (out / (name + ".stdout.log")).write_bytes(completed.stdout)
            (out / (name + ".stderr.log")).write_bytes(completed.stderr)
            report["steps"].append({"name": name, "command": command, "exit_code": completed.returncode})
            require(completed.returncode == 0, "COMPILE_FAILED: " + name)
        assert_aarch64_elf(executable)
        assert_aarch64_elf(validator)
        report.update(status="PASS_COMPILE_ONLY_NOT_RUNTIME",
                      outputs={p.name: {"bytes": p.stat().st_size, "sha256": digest(p)}
                               for p in (executable, validator)})
    finally:
        (out / "BUILD_RESULT.json").write_text(json.dumps(report, indent=2), encoding="utf8")
    print(json.dumps({"status": report["status"], "variant": args.variant, "build_directory": str(out),
                      "SDK_started": False, "hardware_action": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
