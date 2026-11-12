#!/usr/bin/env python3
"""One-click MCU benchmark: read config and metadata, generate a Conan profile, build, then flash."""

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BENCH_DIR  = SCRIPT_DIR.parent
ROOT_DIR   = BENCH_DIR.parent

_BUILD_CPPSTD = None

# Base Firmware owns getTicks() and its unit varies by board; express the verified rates in one canonical unit so every target emits integer ns.
BENCH_TICK_NS_BY_MCU = {
    "cortex-m0": (125, 8),          # BAT32: 64 MHz expanded cycle counter
    "cortex-m3": (1_000_000, 1),    # STM32F205: 1 ms tick
    "cortex-m4": (1_000, 1),        # GD32F470: high-resolution us tick
    "cortex-m7": (1_000_000, 1),    # STM32F767: 1 ms tick
    "cortex-m23": (1_000, 1),       # GD32L233: us tick
    "cortex-m33": (1_000_000, 1),   # STM32U575: 1 ms tick
}

BENCHMARK_TARGET_WINDOW_NS = 100_000_000
BENCHMARK_INITIAL_REPEAT = 100


def build_cppstd():
    """metadata.json owns the standard; build_type stays Release on purpose, since the board benchmarks optimised code."""
    global _BUILD_CPPSTD
    if _BUILD_CPPSTD is None:
        _BUILD_CPPSTD = json.loads((ROOT_DIR / "metadata.json").read_text(encoding="utf-8"))["build_cppstd"]
    return _BUILD_CPPSTD

# Cortex-M target → Conan arch mapping
CONAN_ARCH_MAP = {
    # ── Cortex-M (baremetal) ──────────────────────────────────────
    "cortex-m0":     "armv6",
    "cortex-m0plus": "armv6",
    "cortex-m1":     "armv6",
    "cortex-m3":     "armv7",
    "cortex-m4":     "armv7",
    "cortex-m4f":    "armv7",
    "cortex-m7":     "armv7",
    "cortex-m7f":    "armv7",
    "cortex-m7d":    "armv7",
    "cortex-m23":    "armv8_32",
    "cortex-m33":    "armv8_32",
    "cortex-m33f":   "armv8_32",
    "cortex-m55":    "armv8_32",
    "cortex-m85":    "armv8_32",
    # ── Cortex-A (Linux) ─────────────────────────────────────────
    "cortex-a5":     "armv7",
    "cortex-a7":     "armv7",
    "cortex-a8":     "armv7",
    "cortex-a9":     "armv7",
    "cortex-a15":    "armv7",
    "cortex-a17":    "armv7",
    "cortex-a35":    "armv8",
    "cortex-a53":    "armv8",
    "cortex-a55":    "armv8",
    "cortex-a72":    "armv8",
    "cortex-a73":    "armv8",
    "cortex-a76":    "armv8",
    "cortex-a78":    "armv8",
    # ── RISC-V Linux ────────────────────────────────────────────
    "riscv64":       "riscv64",
    # ── Native Linux host ───────────────────────────────────────
    "x86_64":        "x86_64",
    "native-x86_64": "x86_64",
    "host-x86_64":   "x86_64",
}


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _flags_list(flags: list) -> str:
    return "[" + ",".join(f'"{x}"' for x in flags) + "]"


def _benchmark_timing_options(
    cfg: dict, package_name: str, default_tick_ns: tuple[int, int]
) -> list[str]:
    tick_hz = int(cfg.get("benchmark_tick_hz", 0))
    if tick_hz > 0:
        common = math.gcd(1_000_000_000, tick_hz)
        tick_ns = (1_000_000_000 // common, tick_hz // common)
    else:
        tick_ns = default_tick_ns
    num, den = tick_ns
    if tick_hz <= 0:
        tick_hz = (1_000_000_000 * tick_ns[1]) // tick_ns[0]
    default_target_ticks = max(100, tick_hz // 10)
    target_ticks = int(
        cfg.get("benchmark_target_ticks", default_target_ticks)
    )
    initial_repeat = int(cfg.get("benchmark_initial_repeat", BENCHMARK_INITIAL_REPEAT))
    if num <= 0 or den <= 0 or target_ticks <= 0 or initial_repeat <= 0:
        raise ValueError("benchmark timing fields must be positive")
    return [
        f"{package_name}/*:tick_ns_num={num}",
        f"{package_name}/*:tick_ns_den={den}",
        f"{package_name}/*:target_ticks={target_ticks}",
        f"{package_name}/*:initial_repeat={initial_repeat}",
    ]


def generate_profile(cfg: dict, lib_name: str) -> Path:
    """Generate Conan profile for Cortex-M / baremetal target."""
    mcu               = cfg["target_mcu"]
    float_abi         = cfg.get("float_abi", "soft")
    fpu               = cfg.get("fpu", "none")
    flash_origin      = cfg.get("algo_flash_origin", "0x08030000")
    ram_origin        = cfg.get("algo_ram_origin",   "0x20002000")
    compiler_version  = str(cfg.get("compiler_version", "14"))
    toolchain_version = str(cfg.get("toolchain_package_version", "11.3.rel1"))
    extra_cflags      = cfg.get("extra_cflags", [])

    # `profile_arch` is the physical-core contract and `conan_arch` the ABI-qualified build selector; keep both so package and benchmark resolve the same binary.
    arch     = str(cfg.get("conan_arch") or CONAN_ARCH_MAP.get(mcu, "armv7"))
    pkg_name = f"{lib_name}_bench_mcu"

    flags = [f"-mcpu={mcu}", "-mthumb", f"-mfloat-abi={float_abi}"]
    if fpu != "none":
        flags.append(f"-mfpu={fpu}")
    timing_options = _benchmark_timing_options(
        cfg,
        pkg_name,
        BENCH_TICK_NS_BY_MCU.get(mcu, (1_000, 1)),
    )
    timing_options_text = "".join(f"{option}\n" for option in timing_options)
    flags.extend(extra_cflags)
    flags_str = _flags_list(flags)

    # baremetal needs -fno-exceptions/-fno-rtti at profile level, else .ARM.exidx breaks the link
    cxx_flags_str = _flags_list(flags + ["-fno-exceptions", "-fno-rtti"])

    profile_content = (
        "[settings]\n"
        "os=baremetal\n"
        f"arch={arch}\n"
        "compiler=gcc\n"
        f"compiler.version={compiler_version}\n"
        f"compiler.cppstd={build_cppstd()}\n"
        "compiler.libcxx=libstdc++11\n"
        "build_type=Release\n"
        "\n"
        "[options]\n"
        f"{pkg_name}/*:target_mcu={mcu}\n"
        f"{pkg_name}/*:float_abi={float_abi}\n"
        f"{pkg_name}/*:fpu={fpu}\n"
        f"{pkg_name}/*:algo_flash_origin={flash_origin}\n"
        f"{pkg_name}/*:algo_ram_origin={ram_origin}\n"
        f"{timing_options_text}"
        "\n"
        "[conf]\n"
        "tools.cmake.cmaketoolchain:system_name=Generic\n"
        "tools.cmake.cmaketoolchain:system_processor=arm\n"
        f"tools.build:cflags={flags_str}\n"
        f"tools.build:cxxflags={cxx_flags_str}\n"
        f"tools.build:sharedlinkflags={flags_str}\n"
        f"tools.build:exelinkflags={flags_str}\n"
        "\n"
        "[tool_requires]\n"
        f"arm-toolchain/{toolchain_version}\n"
    )

    profiles_dir = BENCH_DIR / "profiles"
    profiles_dir.mkdir(exist_ok=True)
    profile_path = profiles_dir / f"{mcu}.profile"
    profile_path.write_text(profile_content, encoding="utf-8")
    print(f"[INFO] Profile generated: profiles/{mcu}.profile")
    return profile_path


def build(profile_path: Path) -> None:
    # wipe the build dir between targets: CMake caches are not portable across toolchains
    build_dir = BENCH_DIR / "build"
    if build_dir.exists():
        import shutil
        shutil.rmtree(build_dir)
        print(f"[INFO] Cleaned build directory: {build_dir}")

    rel_profile = profile_path.relative_to(BENCH_DIR)
    cmd = ["conan", "build", ".", f"-pr:h={rel_profile}", "--build=missing"]
    print(f">> {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(BENCH_DIR))


def generate_profile_linux(cfg: dict, lib_name: str) -> Path:
    """Generate Conan profile for Cortex-A / Linux target."""
    cpu               = cfg.get("target_cpu", cfg.get("target_mcu", "cortex-a7"))
    float_abi         = cfg.get("float_abi", "hard")
    fpu               = cfg.get("fpu", "neon-vfpv4")
    compiler_version  = str(cfg.get("compiler_version", "11"))
    toolchain_version = str(cfg.get("toolchain_package_version", "11.3.rel1"))
    extra_cflags      = cfg.get("extra_cflags", [])

    arch = str(cfg.get("profile_arch") or CONAN_ARCH_MAP.get(cpu, "armv7"))
    is_native_x86 = arch == "x86_64"
    is_riscv64 = arch == "riscv64"
    # Conan's Linux/armv8 and armv8.3 profiles select the AArch64 toolchain: -mcpu is valid, ARM32-only float ABI/FPU switches are not.
    is_aarch64 = arch in {"armv8", "armv8.3"}

    flags = []
    if not is_native_x86 and not is_riscv64:
        flags.append(f"-mcpu={cpu}")
    if not is_native_x86 and not is_riscv64 and not is_aarch64 and fpu not in ("none", ""):
        flags += [f"-mfloat-abi={float_abi}", f"-mfpu={fpu}"]
    elif not is_native_x86 and not is_riscv64 and not is_aarch64 and float_abi:
        flags.append(f"-mfloat-abi={float_abi}")
    flags.extend(extra_cflags)
    timing_options = _benchmark_timing_options(
        cfg,
        f"{lib_name}_bench_mcu",
        (1_000, 1),
    )
    flags_str = _flags_list(flags)

    profile_lines = [
        "# AUTO-GENERATED by script/run_bench.py — do not edit manually.",
        "[settings]",
        "os=Linux",
        f"arch={arch}",
        "compiler=gcc",
        f"compiler.version={compiler_version}",
        f"compiler.cppstd={build_cppstd()}",
        "compiler.libcxx=libstdc++11",
        "build_type=Release",
        "",
        "[options]",
        *timing_options,
        "",
        "[conf]",
        f"tools.cmake.cmaketoolchain:extra_variables={{\"TARGET_MCU\": \"{cpu}\"}}",
    ]

    if flags:
        profile_lines.extend(
            [
                f"tools.build:cflags={flags_str}",
                f"tools.build:cxxflags={flags_str}",
                f"tools.build:exelinkflags={flags_str}",
                f"tools.build:sharedlinkflags={flags_str}",
            ]
        )

    if is_native_x86:
        profile_lines.extend(
            [
                f'tools.build:compiler_executables={{"c":"/usr/bin/gcc-{compiler_version}","cpp":"/usr/bin/g++-{compiler_version}"}}',
                "",
                "[buildenv]",
                f"CC=/usr/bin/gcc-{compiler_version}",
                f"CXX=/usr/bin/g++-{compiler_version}",
            ]
        )
    elif is_riscv64:
        profile_lines.extend(
            [
                f'tools.build:compiler_executables={{"c":"/usr/bin/riscv64-linux-gnu-gcc","cpp":"/usr/bin/riscv64-linux-gnu-g++","ar":"/usr/bin/riscv64-linux-gnu-ar","ranlib":"/usr/bin/riscv64-linux-gnu-ranlib","strip":"/usr/bin/riscv64-linux-gnu-strip"}}',
                "",
                "[buildenv]",
                "CC=/usr/bin/riscv64-linux-gnu-gcc",
                "CXX=/usr/bin/riscv64-linux-gnu-g++",
            ]
        )
    else:
        profile_lines.extend(
            [
                "",
                "[tool_requires]",
                f"arm-toolchain/{toolchain_version}",
            ]
        )

    profile_content = "\n".join(profile_lines) + "\n"

    profiles_dir = BENCH_DIR / "profiles"
    profiles_dir.mkdir(exist_ok=True)
    profile_path = profiles_dir / f"{cpu}.profile"
    profile_path.write_text(profile_content, encoding="utf-8")
    print(f"[INFO] Profile generated: profiles/{cpu}.profile")
    return profile_path


def deploy_linux(cfg: dict) -> None:
    """Deploy and run benchmark ELF on A-core Linux target via ADB or SSH."""
    elf_path    = BENCH_DIR / "build" / "Release" / "benchmark"
    deploy_tool = cfg.get("deploy_tool", cfg.get("flash_tool", "adb"))
    remote_path = cfg.get("remote_path", "/data/local/tmp/benchmark")

    if not elf_path.exists():
        print(f"[ERROR] Binary not found: {elf_path}")
        sys.exit(1)

    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "download.py"),
        "--binary", str(elf_path),
        "--mode",   deploy_tool,
        "--remote", remote_path,
        "--run",
    ]

    if deploy_tool == "ssh":
        ssh_host = cfg.get("ssh_host", "")
        if not ssh_host:
            print("[WARN] ssh_host not configured in bench_config — skipping deploy.")
            return
        cmd += ["--host", ssh_host]

    elif deploy_tool == "adb":
        pass  # adb uses default device; no extra args needed
    elif deploy_tool == "local":
        cmd = [str(elf_path)]
        print(f">> {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        return

    print(f">> {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def flash(cfg: dict) -> None:
    bin_path   = BENCH_DIR / "build" / "Release" / "benchmark.bin"
    flash_tool = cfg.get("flash_tool", "jlink")
    flash_addr = cfg.get("algo_flash_origin", "0x08030000")

    if not bin_path.exists():
        print(f"[ERROR] Binary not found: {bin_path}")
        sys.exit(1)

    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "download.py"),
        "--binary", str(bin_path),
        "--mode",   flash_tool,
        "--addr",   flash_addr,
    ]

    if flash_tool == "jlink":
        device = cfg.get("jlink_device", "")
        if not device or device == "CHANGE_ME":
            print("[WARN] jlink_device is not configured in bench_config.json — skipping flash.")
            return
        cmd += ["--device", device]
        cmd += ["--speed", str(cfg.get("jlink_speed", "4000"))]

    elif flash_tool == "openocd":
        target_cfg = cfg.get("openocd_target", "")
        if not target_cfg:
            print("[WARN] openocd_target not configured — skipping flash.")
            return
        cmd += ["--interface", cfg.get("openocd_interface", "interface/stlink.cfg")]
        cmd += ["--target", target_cfg]
        if cfg.get("openocd_cmsis_vid_pid"):
            cmd += ["--cmsis-vid-pid", cfg["openocd_cmsis_vid_pid"]]

    elif flash_tool == "pyocd":
        target = cfg.get("pyocd_target", "")
        if not target:
            print("[WARN] pyocd_target not configured — skipping flash.")
            return
        cmd += ["--target", target]
        if cfg.get("pyocd_probe"):
            cmd += ["--probe", cfg["pyocd_probe"]]

    print(f">> {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser(
        description="One-click MCU benchmark: build + flash."
    )
    parser.add_argument(
        "--config", default="platform/bench_config.json",
        help="Path to hardware config file (default: platform/bench_config.json)"
    )
    parser.add_argument(
        "--no-flash", action="store_true",
        help="Build only, skip the flash step"
    )
    args = parser.parse_args()

    cfg_path = BENCH_DIR / args.config
    if not cfg_path.exists():
        print(f"[ERROR] Config not found: {cfg_path}")
        print("  Copy platform/bench_config.json and edit it for your board.")
        sys.exit(1)

    cfg      = load_json(cfg_path)
    meta     = load_json(ROOT_DIR / "metadata.json")
    lib_name = meta.get("name", "lib")

    target_os    = cfg.get("target_os", "baremetal")
    is_linux     = target_os == "Linux"

    profile_path = (generate_profile_linux(cfg, lib_name)
                    if is_linux
                    else generate_profile(cfg, lib_name))
    build(profile_path)

    if not args.no_flash:
        if is_linux:
            deploy_linux(cfg)
        else:
            flash(cfg)
    else:
        print("[INFO] --no-flash specified, skipping download step.")


if __name__ == "__main__":
    main()
