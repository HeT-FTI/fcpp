#!/usr/bin/env python3
"""Online cross-compile check: build the library through Conan with a cross profile.

Mirrors the template's own conventions:
  * the flow is README's `conan create . -pr:b=default -pr:h=<profile> -tf=""`;
  * the profile fields and the CPU -> conan `arch` mapping follow
    `benchmark/script/run_bench.py` (os/arch/-mcpu/-mthumb/-mfloat-abi/-mfpu,
    `system_name=Generic` for baremetal, `-fno-exceptions -fno-rtti` for C++);
  * the artifact is located the same way the test package does it.

No board and no test package: this only answers "does the target toolchain turn
the real sources into archives of the expected architecture".

Usage: cross_compile_check.py --target {arm-linux-a53,mcu-m4} [--out DIR]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# CPU -> conan arch, copied from the mapping benchmark/script/run_bench.py owns.
CONAN_ARCH = {'cortex-a53': 'armv8', 'cortex-m4': 'armv7'}

TARGETS = {
    'arm-linux-a53': {
        'desc': 'Cortex-A / arm-linux (A-core, OS present)',
        'packages': 'gcc-aarch64-linux-gnu',
        'cc': 'aarch64-linux-gnu-gcc',
        'cxx': 'aarch64-linux-gnu-g++',
        'ar': 'aarch64-linux-gnu-ar',
        'os': 'Linux',
        'mcpu': 'cortex-a53',
        'system_processor': 'aarch64',
        'baremetal': False,
        'expect_class': 'ELF64',
        'expect_machine': 'AArch64',
        'expect_thumb': False,
    },
    'mcu-m4': {
        'desc': 'Cortex-M / baremetal (M-core, no OS)',
        'packages': 'gcc-arm-none-eabi',
        'cc': 'arm-none-eabi-gcc',
        'cxx': 'arm-none-eabi-g++',
        'ar': 'arm-none-eabi-ar',
        'os': 'baremetal',
        'mcpu': 'cortex-m4',
        'float_abi': 'hard',
        'fpu': 'fpv4-sp-d16',
        'system_processor': 'arm',
        'baremetal': True,
        'expect_class': 'ELF32',
        'expect_machine': 'ARM',
        'expect_thumb': True,
    },
}


def _tool(args):
    return subprocess.run(args, capture_output=True, text=True)


def _conan_flags_list(flags):
    """Conan conf lists use the `["a","b"]` form (same as run_bench.py)."""
    return '[' + ','.join(f'"{f}"' for f in flags) + ']'


def _compiler_major(exe):
    """Derive the major version from the toolchain instead of guessing it."""
    out = _tool([exe, '-dumpversion'])
    if out.returncode != 0:
        raise RuntimeError(f'cannot run {exe}: {out.stderr.strip()}')
    return re.split(r'[.\-]', out.stdout.strip())[0]


def generate_profile(cfg, path):
    # -mthumb/-mfloat-abi/-mfpu are AArch32/M-profile flags: valid for the
    # Cortex-M leg, rejected by the A-core one, so only the MCU leg gets them.
    if cfg['baremetal']:
        cpu_flags = [f"-mcpu={cfg['mcpu']}", '-mthumb', f"-mfloat-abi={cfg['float_abi']}"]
        if cfg['fpu'] != 'none':
            cpu_flags.append(f"-mfpu={cfg['fpu']}")
    else:
        cpu_flags = [f"-mcpu={cfg['mcpu']}"]

    cflags = list(cpu_flags)
    cxxflags = list(cpu_flags)
    if cfg['baremetal']:
        # Per run_bench.py: must be set at profile level, otherwise the static
        # library keeps its .ARM.exidx tables.
        cxxflags += ['-fno-exceptions', '-fno-rtti']

    lines = [
        '[settings]',
        f"os={cfg['os']}",
        f"arch={CONAN_ARCH[cfg['mcpu']]}",
        'compiler=gcc',
        f"compiler.version={_compiler_major(cfg['cc'])}",
        'compiler.cppstd=17',
        'compiler.libcxx=libstdc++11',
        'build_type=Release',
        '',
        '[conf]',
    ]
    if cfg['baremetal']:
        lines += ['tools.cmake.cmaketoolchain:system_name=Generic']
    lines += [f"tools.cmake.cmaketoolchain:system_processor={cfg['system_processor']}"]
    lines += [
        'tools.build:compiler_executables={'
        f'"c":"{cfg["cc"]}","cpp":"{cfg["cxx"]}"}}',
        f'tools.build:cflags={_conan_flags_list(cflags)}',
        f'tools.build:cxxflags={_conan_flags_list(cxxflags)}',
        '',
    ]
    Path(path).write_text('\n'.join(lines), encoding='utf-8')


def run(cmd, **kw):
    print(f'>> {" ".join(cmd)}', flush=True)
    return subprocess.run(cmd, check=True, **kw)


def package_folder(lib_name, lib_ver):
    """Same derivation the test package uses: `conan cache path <ref>`."""
    refs = [s.strip() for s in _tool(['conan', 'list', f'{lib_name}/{lib_ver}:*']).stdout.split('\n')]
    marks = [i for i, s in enumerate(refs) if s == 'packages']
    if not marks:
        raise RuntimeError(f'conan list {lib_name}/{lib_ver}:* listed no package')
    pkg_uid = refs[marks[0] + 1]
    folder = _tool(['conan', 'cache', 'path', f'{lib_name}/{lib_ver}:{pkg_uid}']).stdout.strip()
    if not folder:
        raise RuntimeError(f'conan cache path {lib_name}/{lib_ver}:{pkg_uid} returned nothing')
    return folder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', required=True, choices=sorted(TARGETS))
    ap.add_argument('--out', default='build-cross')
    args = ap.parse_args()
    cfg = TARGETS[args.target]

    for exe in (cfg['cc'], cfg['cxx'], cfg['ar']):
        if shutil.which(exe) is None:
            raise RuntimeError(f'{exe} is not on PATH (expected from {cfg["packages"]})')

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    profile = out / f'{args.target}.profile'
    generate_profile(cfg, profile)
    print(f'--- generated profile ({args.target}) ---')
    print(profile.read_text(encoding='utf-8'))

    meta = json.loads(Path('metadata.json').read_text(encoding='utf-8'))
    lib_name, lib_ver = meta['name'], meta['version']

    # README's documented cross-build: host profile + cross profile + no test folder.
    run(['conan', 'create', '.', '-pr:b=default', f'-pr:h={profile}',
         '-tf=', '--build=missing'])

    pkg_folder = package_folder(lib_name, lib_ver)
    print(f'package folder: {pkg_folder}')

    archives = sorted(Path(pkg_folder, 'lib').glob('*.a'))
    if not archives:
        raise RuntimeError(f'no static archive produced under {pkg_folder}/lib')

    status = 0
    report = []
    with tempfile.TemporaryDirectory() as tmp:
        for archive in archives:
            run([cfg['ar'], 'x', str(archive)], cwd=tmp)
            members = sorted(p for p in Path(tmp).iterdir()
                             if p.is_file() and 'CompilerId' not in p.name)
            if not members:
                raise RuntimeError(f'{archive.name} has no member to inspect')
            member = members[0]

            header = _tool(['readelf', '-h', str(member)]).stdout
            attrs = _tool(['readelf', '-A', str(member)]).stdout
            # capture the whole value: some machine names are multi-word
            elf_class = (re.search(r'^\s*Class:\s*(.+?)\s*$', header, re.M) or [None, ''])[1]
            machine = (re.search(r'^\s*Machine:\s*(.+?)\s*$', header, re.M) or [None, ''])[1]
            cpu_arch = (re.search(r'^\s*Tag_CPU_arch:\s*(.+)$', attrs, re.M) or [None, '-'])[1]
            thumb = (re.search(r'^\s*Tag_THUMB_ISA_use:\s*(.+)$', attrs, re.M) or [None, '-'])[1]

            ok_class = cfg['expect_class'] in elf_class
            ok_machine = cfg['expect_machine'] in machine
            ok_thumb = (not cfg['expect_thumb']) or thumb != '-'
            for ok, what, got, want in (
                (ok_class, 'Class', elf_class, cfg['expect_class']),
                (ok_machine, 'Machine', machine, cfg['expect_machine']),
                (ok_thumb, 'Thumb ISA', thumb, 'present (Cortex-M requires Thumb code)'),
            ):
                if not ok:
                    print(f'::error::{archive.name}: expected {what} {want}, got {got}')
                    status = 1
            report.append((archive.name, member.name, elf_class, machine, cpu_arch, thumb))
            member.unlink()

    print(f'--- {args.target}: {cfg["desc"]} ---')
    for name, member, elf_class, machine, cpu_arch, thumb in report:
        print(f'  {name} -> {member}: Class={elf_class} Machine={machine} '
              f'Tag_CPU_arch={cpu_arch} Tag_THUMB_ISA_use={thumb}')

    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as fh:
            fh.write(f'### Cross compile ({args.target})\n\n')
            fh.write(f'{cfg["desc"]} -- built with Conan using a generated profile '
                     f'(`os={cfg["os"]}`, `arch={CONAN_ARCH[cfg["mcpu"]]}`, '
                     f'`-mcpu={cfg["mcpu"]}`, `-tf=` so no test folder).\n\n')
            fh.write('| archive | member | Class | Machine | Tag_CPU_arch | Tag_THUMB_ISA_use |\n')
            fh.write('| --- | --- | --- | --- | --- | --- |\n')
            for name, member, elf_class, machine, cpu_arch, thumb in report:
                fh.write(f'| {name} | {member} | {elf_class} | {machine} | {cpu_arch} | {thumb} |\n')
            fh.write('\n')

    return status


if __name__ == '__main__':
    sys.exit(main())
