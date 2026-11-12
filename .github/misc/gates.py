#!/usr/bin/env python3
"""Native quality gates: one implementation behind CI, pre-commit and the editor task panel."""
import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# One gate implementation for CI, pre-commit and the editor; standards and switches come from metadata.json.

# Usage: gates.py {format|tidy|guard|board|neutral|all} [FILE ...]; exit 0 clean, 1 failed, 2 setup.

# tidy roots: -Iinclude, EIGEN_INC, GATES_INCLUDE, -isystem $ETL_INC; header-only packages get one synthetic TU per header.

ROOT = Path(__file__).resolve().parents[2]
GATE_DIRS = ('include', 'src')
TIDY_DIRS = ('src',)
C_SUFFIXES = ('.c', '.h')
CPP_SUFFIXES = ('.cpp', '.hpp')
FORMAT_C = '.github/misc/.clang-format-c'
FORMAT_CPP = '.github/misc/.clang-format-cpp'
TIDY_CONFIG = '.github/misc/.clang-tidy'
GUARD_CONFIG = '.github/misc/.include-guard.txt'

_META = None


def _die(message):
    """Setup error: never report this as a gate verdict."""
    print(f'[gates] error: {message}', file=sys.stderr, flush=True)
    raise SystemExit(2)


def _rel(path):
    """Repo-relative posix path, so output is stable on every platform."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _meta(key, default=None):
    """One metadata.json field, read once: the single source, never re-typed here."""
    global _META
    if _META is None:
        try:
            _META = json.loads((ROOT / 'metadata.json').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            _die(f'cannot read metadata.json: {exc}')
        if not isinstance(_META, dict):
            _die('metadata.json is not a JSON object')
    return _META.get(key, default)


def _standards():
    """C/C++ standards from metadata.json."""
    return str(_meta('build_cstd', '11')), str(_meta('build_cppstd', '17'))


def _require(tool, hint):
    exe = shutil.which(tool)
    if not exe:
        _die(f'{tool} not found on PATH; {hint}')
    return exe


def _version(exe):
    out = subprocess.run([exe, '--version'], capture_output=True, text=True)
    lines = (out.stdout or out.stderr).strip().splitlines()
    return lines[0] if lines else 'unknown'


def _gated_files(explicit, dirs):
    """C/C++ files under the given gate dirs, or just the given paths that live in them."""
    candidates = []
    if explicit:
        for item in explicit:
            path = Path(item)
            if not path.is_absolute():
                path = ROOT / path
            if path.is_file():
                candidates.append(path.resolve())
    else:
        for name in dirs:
            base = ROOT / name
            if base.is_dir():
                candidates.extend(p.resolve() for p in base.rglob('*') if p.is_file())
    files = []
    for path in candidates:
        if path.suffix not in C_SUFFIXES + CPP_SUFFIXES:
            continue
        try:
            if path.relative_to(ROOT).parts[0] not in dirs:
                continue
        except ValueError:
            continue
        files.append(path)
    return sorted(set(files))


def _empty(explicit, gate, dirs):
    """A whole-tree run must never pass vacuously; an explicit file list may be a no-op."""
    scope = ', '.join(f'{name}/' for name in dirs)
    if explicit:
        print(f'[gates] {gate}: nothing to check in the given file list (scope: {scope})', flush=True)
        return 0
    print(f'[gates] {gate}: FAIL -- no C/C++ file found under {scope}', file=sys.stderr, flush=True)
    return 1


def _format_cmd(exe, path, fix):
    """.c/.h route to the C config, .cpp/.hpp to the C++ one (clang-format maps .h to C++)."""
    style = FORMAT_C if path.suffix in C_SUFFIXES else FORMAT_CPP
    cmd = [exe, f'--style=file:{style}']
    cmd += ['-i'] if fix else ['--dry-run', '--Werror']
    cmd.append(_rel(path))
    return cmd


def cmd_format(explicit, fix=False, jobs=1):
    files = _gated_files(explicit, GATE_DIRS)
    if not files:
        return _empty(explicit, 'format', GATE_DIRS)
    exe = _require('clang-format', 'install it with `pip install clang-format==23.1.0` (>= 19 needed)')
    print(f'[gates] format({"fix" if fix else "check"}): {len(files)} file(s), {_version(exe)}', flush=True)
    with ThreadPoolExecutor(max_workers=max(jobs, 1)) as pool:
        results = list(pool.map(
            lambda p: (p, subprocess.run(_format_cmd(exe, p, fix), cwd=ROOT, capture_output=True, text=True)),
            files,
        ))
    failed = []
    for path, done in results:
        if done.returncode != 0:
            failed.append(path)
            print(f'--- {_rel(path)}', flush=True)
            print((done.stdout + done.stderr).strip(), flush=True)
    if failed:
        print(f'[gates] format: FAIL ({len(failed)}/{len(files)} file(s))', flush=True)
        return 1
    print(f'[gates] format: OK ({len(files)} file(s))', flush=True)
    return 0


def _tidy_flags(extra_include, extra_isystem):
    """Header roots src/ needs that no -I in the repo provides (CI exports ETL_INC)."""
    includes = ['include']
    eigen = os.environ.get('EIGEN_INC') or '/usr/include/eigen3'
    generic = os.environ.get('GATES_INCLUDE', '').split(os.pathsep)
    for path in [eigen] + generic + list(extra_include):
        if path and Path(path).is_dir() and path not in includes:
            includes.append(path)
    etl = os.environ.get('ETL_INC')
    if etl and not Path(etl).is_dir():
        print(f'[gates] warning: ETL_INC={etl} is not a directory', file=sys.stderr, flush=True)
    isystems = []
    for path in [etl] + list(extra_isystem):
        if path and Path(path).is_dir() and path not in isystems:
            isystems.append(path)
    return includes, isystems


def _tidy_cmd(exe, path, cstd, cppstd, includes, isystems):
    std = f'-std=c{cstd}' if path.suffix in C_SUFFIXES else f'-std=c++{cppstd}'
    cmd = [exe, f'--config-file={TIDY_CONFIG}', _rel(path), '--', std]
    cmd += [f'-I{d}' for d in includes]
    for dir_path in isystems:
        cmd += ['-isystem', dir_path]
    return cmd


def _header_units(headers, workdir):
    """One synthetic TU per public header; compiling a header alone also proves self-containment."""
    units = []
    for index, header in enumerate(headers):
        suffix = '.c' if header.suffix in C_SUFFIXES else '.cpp'
        tu = Path(workdir) / f'tu_{index:03d}{suffix}'
        # include through the -Iinclude root so diagnostics name the real header path
        rel = header.relative_to(ROOT / 'include').as_posix()
        tu.write_text(f'#include "{rel}"\n', encoding='utf-8')
        units.append((header, tu))
    return units


def _tidy_run(exe, pairs, cstd, cppstd, includes, isystems):
    """pairs = (label, file to compile); returns the labels that failed."""
    failed = []
    for label, path in pairs:
        done = subprocess.run(
            _tidy_cmd(exe, path, cstd, cppstd, includes, isystems),
            cwd=ROOT, capture_output=True, text=True,
        )
        if done.returncode != 0:
            failed.append(label)
            print(f'--- {_rel(label)}', flush=True)
            print((done.stdout + done.stderr).strip(), flush=True)
    return failed


def cmd_tidy(explicit, extra_include=(), extra_isystem=()):
    units = _gated_files(explicit, TIDY_DIRS)
    headers = _gated_files(explicit, ('include',))
    if explicit:
        plan_units, plan_headers = units, headers
    elif _meta('is_header') or not units:
        plan_units, plan_headers = [], headers
    else:
        plan_units, plan_headers = units, []
    if not plan_units and not plan_headers:
        return _empty(explicit, 'tidy', TIDY_DIRS + ('include',))
    exe = _require('clang-tidy', 'install it with `apt-get install clang-tidy`')
    cstd, cppstd = _standards()
    includes, isystems = _tidy_flags(extra_include, extra_isystem)
    total = len(plan_units) + len(plan_headers)
    print(f'[gates] tidy: {total} target(s), {_version(exe)}, -std=c{cstd} / -std=c++{cppstd}', flush=True)
    failed = _tidy_run(exe, [(p, p) for p in plan_units], cstd, cppstd, includes, isystems)
    if plan_headers:
        # header-only package (or a header passed explicitly): no TU of its own to inspect
        print(f'[gates] tidy: header mode -- {len(plan_headers)} synthetic TU(s), one per public header', flush=True)
        with tempfile.TemporaryDirectory(prefix='gates-tidy-') as workdir:
            pairs = _header_units(plan_headers, workdir)
            failed += _tidy_run(exe, pairs, cstd, cppstd, includes, isystems)
    if failed:
        print(f'[gates] tidy: FAIL ({len(failed)}/{total} file(s))', flush=True)
        return 1
    print(f'[gates] tidy: OK ({total} file(s))', flush=True)
    return 0


def _guard_rules():
    """(scope glob, compiled regex) pairs from .github/misc/.include-guard.txt."""
    config = ROOT / GUARD_CONFIG
    if not config.is_file():
        _die(f'{GUARD_CONFIG} not found; it lists "<scope glob> <forbidden include regex>" per line')
    rules = []
    for lineno, raw in enumerate(config.read_text(encoding='utf-8').splitlines(), start=1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            _die(f'{GUARD_CONFIG}:{lineno}: expected "<scope glob> <regex>", got {raw!r}')
        scope, pattern = parts
        try:
            rules.append((scope, re.compile(pattern)))
        except re.error as exc:
            _die(f'{GUARD_CONFIG}:{lineno}: bad regex {pattern!r}: {exc}')
    if not rules:
        _die(f'{GUARD_CONFIG} defines no rule')
    return rules


def _scoped_name(path):
    """Path relative to include/ or src/ -- what the guard scopes are written against."""
    for name in GATE_DIRS:
        try:
            return path.resolve().relative_to(ROOT / name).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _include_directives(path):
    """(line number, included path) for every #include directive in the file."""
    pattern = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
    found = []
    with open(path, encoding='utf-8', errors='replace') as handle:
        for lineno, line in enumerate(handle, start=1):
            match = pattern.match(line)
            if match:
                found.append((lineno, match.group(1)))
    return found


def cmd_guard(explicit):
    """Reject forbidden includes; rules live in .github/misc/.include-guard.txt."""
    files = _gated_files(explicit, GATE_DIRS)
    if not files:
        return _empty(explicit, 'guard', GATE_DIRS)
    rules = _guard_rules()
    print(f'[gates] guard: {len(files)} file(s), {len(rules)} rule(s) from {GUARD_CONFIG}', flush=True)
    violations = 0
    for path in files:
        scoped = _scoped_name(path)
        active = [regex for scope, regex in rules if fnmatch.fnmatch(scoped, scope)]
        for lineno, included in _include_directives(path) if active else []:
            for regex in active:
                if regex.search(included):
                    violations += 1
                    print(f'{_rel(path)}:{lineno}: forbidden include <{included}> ({regex.pattern})', flush=True)
    if violations:
        print(f'[gates] guard: FAIL ({violations} violation(s))', flush=True)
        return 1
    print(f'[gates] guard: OK ({len(files)} file(s))', flush=True)
    return 0


BOARD_MATRIX = '.github/misc/supportable-board-matrix.yml'
BOARD_REQUIRED = ('id', 'target_key', 'build_kind', 'os', 'arch', 'toolchain_versions', 'benchmark')

# Phrases that would teach a derived project the wrong name (identity fields and the contract note are exempt).
NEUTRAL_PATTERNS = (
    r'for the fcpp template|fcpp template|fcpp drives|fcpp 模板|'
    r'fcpp_c\b|fcpp_cpp\b|fcpp_vec_|fcpp import-guard|fcpp-convention|^# fcpp '
)
PATH_PATTERNS = r'/home/(zc|fcpp)'
# Identity fields: the template's own name/URL, which every instance must change once.
NEUTRAL_IDENTITY = {
    'metadata.json', 'package.json', 'package-lock.json', 'README.md', 'CHANGELOG.md',
    'NOTICE', 'LICENSE', '.github/skills/manifest.json',
}
# Files that are allowed to name the upstream project on a provenance line.
NEUTRAL_PROVENANCE = {'.github/misc/Codegen-Starter.txt': 'Based on:'}
# The gate itself spells the forbidden phrases; scanning it would always fail.
NEUTRAL_SKIP = {'.github/misc/gates.py'}
# Guidance that ships downstream; library sources keep this template's sample symbols, so they are content.
NEUTRAL_SCOPE = ('.github/skills', '.github/misc/Codegen-Starter.txt', '.github/CONTRIBUTING.md')


def cmd_board():
    """Guard the gold standard, citations, mapping and metadata selection against drift."""
    try:
        import yaml
    except ImportError:
        _die('PyYAML is required for the board gate: pip install pyyaml')
    path = ROOT / BOARD_MATRIX
    if not path.is_file():
        _die(f'{BOARD_MATRIX} not found')
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        _die(f'{BOARD_MATRIX}: root must be a mapping')
    targets = data.get('targets')
    if not isinstance(targets, list) or not targets:
        _die(f'{BOARD_MATRIX}: targets must be a non-empty list')

    problems = []
    ids = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            problems.append(f'targets[{index}] is not a mapping')
            continue
        missing = [key for key in BOARD_REQUIRED if key not in target]
        if missing:
            problems.append(f'targets[{index}] missing {", ".join(missing)}')
            continue
        target_id = target['id']
        if target_id in ids:
            problems.append(f'duplicate target id {target_id!r}')
        ids.append(target_id)
        for dead in ('default', 'enabled', 'package_ref'):
            if dead in target:
                problems.append(f'{target_id}: dead field {dead!r} must not be declared')
        versions = target['toolchain_versions']
        if not isinstance(versions, list) or not versions:
            problems.append(f'{target_id}: toolchain_versions must be a non-empty list')
        benchmark = target['benchmark']
        if not isinstance(benchmark, dict) or not isinstance(benchmark.get('enabled'), bool):
            problems.append(f'{target_id}: benchmark.enabled must be a boolean')
        elif target['build_kind'] == 'linux' and str(benchmark.get('profile_arch', '')).startswith('cortex-'):
            problems.append(f'{target_id}: benchmark.profile_arch must be a Conan arch family, not a core')

    if 'linux-riscv64' in ids:
        problems.append('linux-riscv64 is reserved and must not be in the gold standard')
    if len(ids) != 12:
        problems.append(f'expected 12 hardware-verified targets, found {len(ids)}')

    verified = data.get('verified')
    if not isinstance(verified, list) or not verified:
        problems.append('verified must be a non-empty list of citations')
        verified = []
    cited = set()
    for index, record in enumerate(verified):
        if not isinstance(record, dict):
            problems.append(f'verified[{index}] is not a mapping')
            continue
        for key in ('repo', 'run', 'date', 'targets', 'scope'):
            if key not in record:
                problems.append(f'verified[{index}] missing {key!r}')
        record_ids = record.get('targets') or []
        unknown = [item for item in record_ids if item not in ids]
        if unknown:
            problems.append(f'verified[{index}] cites unknown target(s): {", ".join(unknown)}')
        cited.update(record_ids)
    if set(ids) - cited:
        problems.append('targets without a citation: ' + ', '.join(sorted(set(ids) - cited)))

    sys.path.insert(0, str(ROOT / '.github/misc'))
    try:
        import on_board_analyzer
    except ImportError as exc:
        _die(f'cannot import on_board_analyzer: {exc}')
    mapped = {entry['id'] for entry in on_board_analyzer.TARGET_MAP.values()}
    siblings = {
        item
        for entry in on_board_analyzer.TARGET_MAP.values()
        for item in entry.get('siblings', [])
    }
    if mapped - set(ids):
        problems.append('mapping points outside the gold standard: ' + ', '.join(sorted(mapped - set(ids))))
    if set(ids) - mapped - siblings:
        problems.append(
            'gold targets that are neither mapped nor declared as siblings: '
            + ', '.join(sorted(set(ids) - mapped - siblings))
        )

    triggers = _meta('workflow_triggers') or {}
    selection = _meta('on_board_validation')
    if triggers.get('on_board') is True:
        if selection is None:
            problems.append('workflow_triggers.on_board=true requires on_board_validation')
        else:
            try:
                on_board_analyzer.resolve(
                    on_board_analyzer.validate_selection(selection),
                    {target['id']: target for target in targets},
                )
            except (on_board_analyzer.UsageError, on_board_analyzer.ResolutionError) as exc:
                problems.append(f'on_board_validation does not resolve: {exc}')

    if problems:
        for problem in problems:
            print(f'[gates] board: {problem}', file=sys.stderr, flush=True)
        print(f'[gates] board: FAIL ({len(problems)} problem(s))', flush=True)
        return 1
    print(f'[gates] board: OK ({len(ids)} targets, {len(verified)} citation(s))', flush=True)
    return 0


def _tracked_grep(pattern):
    done = subprocess.run(
        ['git', 'grep', '-n', '-I', '-E', pattern, '--', *NEUTRAL_SCOPE],
        cwd=ROOT, capture_output=True, text=True,
    )
    if done.returncode not in (0, 1):
        _die(f'git grep failed: {done.stderr.strip() or done.returncode}')
    return [line for line in done.stdout.splitlines() if line]


def cmd_neutral():
    """Keep the template name out of anything a derived project would copy verbatim."""
    violations = []
    for line in _tracked_grep(NEUTRAL_PATTERNS) + _tracked_grep(PATH_PATTERNS):
        rel, _, rest = line.partition(':')
        lineno, _, content = rest.partition(':')
        if rel in NEUTRAL_SKIP or rel in NEUTRAL_IDENTITY:
            continue
        for path, marker in NEUTRAL_PROVENANCE.items():
            if rel == path and marker in content:
                break
        else:
            violations.append(f'{rel}:{lineno}: {content.strip()}')
    if violations:
        for item in violations:
            print(f'[gates] neutral: {item}', flush=True)
        print(f'[gates] neutral: FAIL ({len(violations)} hit(s))', flush=True)
        return 1
    print('[gates] neutral: OK', flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description='Native quality gates (one implementation, three callers).')
    sub = parser.add_subparsers(dest='gate', required=True)
    helps = {
        'format': 'clang-format --dry-run --Werror over include/ + src/',
        'tidy': 'clang-tidy (WarningsAsErrors) over src/ TUs; header-only: one synthetic TU per header',
        'guard': 'forbidden #include directives, scoped rules from .github/misc/.include-guard.txt',
        'board': 'gold-standard, mapping and on_board_validation consistency',
        'neutral': 'template-name leakage and machine-path checks over tracked files',
        'all': 'format + tidy + guard',
    }
    for name, text in helps.items():
        leaf = sub.add_parser(name, help=text)
        leaf.add_argument('files', nargs='*', help='check only these files (default: every gated file)')
        if name == 'format':
            leaf.add_argument('--fix', action='store_true', help='rewrite files instead of --dry-run')
            leaf.add_argument('--jobs', type=int, default=os.cpu_count() or 1, help='parallel clang-format workers')
        if name == 'tidy':
            leaf.add_argument('--include', action='append', default=[], metavar='DIR', help='extra -I (repeatable)')
            leaf.add_argument('--isystem', action='append', default=[], metavar='DIR', help='extra -isystem (repeatable)')
    args = parser.parse_args()

    if args.gate == 'format':
        return cmd_format(args.files, args.fix, args.jobs)
    if args.gate == 'tidy':
        return cmd_tidy(args.files, args.include, args.isystem)
    if args.gate == 'guard':
        return cmd_guard(args.files)
    if args.gate == 'board':
        return cmd_board()
    if args.gate == 'neutral':
        return cmd_neutral()
    return 1 if (cmd_format([]) or cmd_tidy([]) or cmd_guard([])) else 0


if __name__ == '__main__':
    sys.exit(main())
