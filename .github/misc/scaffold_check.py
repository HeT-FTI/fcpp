#!/usr/bin/env python3
"""Compare scaffold files byte-for-byte with the template release named in metadata.scaffold."""
import argparse
import difflib
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UPSTREAM_URL = "https://github.com/HeT-FTI/fcpp.git"
MANIFEST = REPO / ".github/project/scaffold.json"
BASELINE = REPO / ".github/project/scaffold-baseline"
PATHS_FILE = ".github/misc/scaffold-paths.txt"


def run(cmd, cwd=None, check=True):
    """Run a command and return stdout, raising a token-safe message on failure."""
    done = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and done.returncode != 0:
        token = os.environ.get("SCAFFOLD_TOKEN", "")
        output = done.stdout.replace(token, "***")
        raise RuntimeError(f"{' '.join(cmd).replace(token, '***')} failed:\n{output}")
    return done


def fail(message):
    """Print one actionable drift line and exit non-zero."""
    print(f"scaffold drift: {message}", file=sys.stderr)
    print("revert it or upstream the change and bump metadata.scaffold", file=sys.stderr)
    return 1


def load_metadata():
    """Read metadata.json once; the scaffold tag and the upstream name live there."""
    with (REPO / "metadata.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def tree_files(root):
    """List repository-relative files, using git when available and os.walk for baselines."""
    root = Path(root)
    if (root / ".git").exists():
        out = run(["git", "-C", str(root), "ls-files", "-z"]).stdout
        return {item for item in out.split("\0") if item}
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }


def load_patterns(root):
    """Read the upstream-owned checked-path patterns; a missing manifest is a hard failure."""
    path = Path(root) / PATHS_FILE
    if not path.is_file():
        raise RuntimeError(f"{PATHS_FILE} is missing from the pinned upstream tree")
    patterns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            patterns.append(line)
    if not patterns:
        raise RuntimeError(f"{PATHS_FILE} contains no patterns")
    return patterns


def matches(path, patterns):
    """Match a posix path against the scaffold patterns."""
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def fetch_upstream(tag, workdir):
    """Clone the pinned tag; SCAFFOLD_TOKEN lifts the rate limit for private mirrors."""
    explicit = os.environ.get("SCAFFOLD_SOURCE")
    if explicit:
        source = Path(explicit)
        if not source.is_dir():
            raise RuntimeError(f"SCAFFOLD_SOURCE is not a directory: {source}")
        return source
    dest = Path(workdir) / "upstream"
    url = UPSTREAM_URL
    token = os.environ.get("SCAFFOLD_TOKEN")
    if token and url.startswith("https://"):
        url = url.replace("https://", f"https://x-access-token:{token}@", 1)
    run(["git", "clone", "--depth", "1", "--branch", f"v{tag}", url, str(dest)])
    return dest


def fallback_baseline(tag):
    """Return the offline baseline tree for this tag, or None when it was never stored."""
    for candidate in (BASELINE / f"v{tag}", BASELINE):
        if candidate.is_dir() and (candidate / PATHS_FILE).is_file():
            return candidate
    return None


def diff_excerpt(local, upstream):
    """Show the first lines of a unified diff so the failure names the actual change."""
    try:
        local_text = Path(local).read_text(encoding="utf-8", errors="replace").splitlines()
        upstream_text = Path(upstream).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [f"<unreadable: {exc}>"]
    lines = list(
        difflib.unified_diff(upstream_text, local_text, fromfile="upstream", tofile="local", lineterm="")
    )
    return lines[:40] if lines else ["<binary files differ>"]


def compare_tree(upstream, patterns):
    """Compare every upstream-owned file and report missing, changed and extra paths."""
    upstream_files = {path for path in tree_files(upstream) if matches(path, patterns)}
    local_files = {path for path in tree_files(REPO) if matches(path, patterns)}
    problems = []
    for relative in sorted(upstream_files):
        expected = Path(upstream) / relative
        actual = REPO / relative
        if not actual.is_file():
            problems.append((relative, ["<missing locally>"]))
            continue
        if expected.read_bytes() != actual.read_bytes():
            problems.append((relative, diff_excerpt(actual, expected)))
    for relative in sorted(local_files - upstream_files):
        problems.append((relative, ["<not present in the pinned release>"]))
    return problems, upstream_files


def read_tolerated():
    """Load declared forks; every entry must carry patch, why and exit."""
    if not MANIFEST.is_file():
        return [], None
    with MANIFEST.open(encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data.get("tolerated", [])
    if not isinstance(entries, list):
        raise RuntimeError("scaffold.json tolerated must be a list")
    return entries, data.get("tolerated_budget")


def patch_entry(entry, upstream, workdir):
    """Apply one tolerated patch to a clean upstream copy and return the resulting bytes."""
    for key in ("path", "patch", "why", "exit"):
        if not str(entry.get(key, "")).strip():
            raise RuntimeError(f"tolerated entry is missing {key}: {entry}")
    relative = entry["path"]
    patch_path = REPO / entry["patch"]
    if not patch_path.is_file():
        raise RuntimeError(f"tolerated patch not found: {patch_path}")
    scratch = Path(workdir) / "patch"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir()
    target = scratch / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(upstream) / relative, target)
    run(["git", "init", "-q"], cwd=scratch)
    run(["git", "add", "-A"], cwd=scratch)
    run(["git", "apply", "--unsafe-paths", str(patch_path)], cwd=scratch)
    return target.read_bytes()


def check_tolerated(upstream, checked_upstream, workdir):
    """Reject stale, unreproducible, or unreviewed tolerated entries."""
    entries, budget = read_tolerated()
    problems = []
    for entry in entries:
        relative = str(entry.get("path", ""))
        if relative not in checked_upstream:
            problems.append((relative or "<empty>", ["<tolerated path is not in the checked set>"]))
            continue
        actual = REPO / relative
        if not actual.is_file():
            problems.append((relative, ["<tolerated path is missing locally>"]))
            continue
        if actual.read_bytes() == (Path(upstream) / relative).read_bytes():
            problems.append((relative, ["<tolerated entry is stale: it already matches upstream>"]))
            continue
        try:
            patched = patch_entry(entry, upstream, workdir)
        except RuntimeError as exc:
            problems.append((relative, [f"<patch does not apply: {exc}>"]))
            continue
        if patched != actual.read_bytes():
            problems.append((relative, ["<patch result does not match the local file>"]))
    if isinstance(budget, int) and len(entries) > budget:
        problems.append((".github/project/scaffold.json", [f"<tolerated grew to {len(entries)} > budget {budget}>"]))
    return problems


def check_staged(patterns, tag):
    """Offline pre-commit mode: compare only staged scaffold files against the baseline."""
    baseline = fallback_baseline(tag)
    if baseline is None:
        print(f"::notice::no scaffold-baseline for v{tag}; staged check skipped")
        return 0
    out = run(["git", "-C", str(REPO), "diff", "--cached", "--name-only", "-z"]).stdout
    staged = {item for item in out.split("\0") if item and matches(item, patterns)}
    if not staged:
        print("scaffold staged check: no scaffold files staged")
        return 0
    problems = []
    for relative in sorted(staged):
        expected = Path(baseline) / relative
        actual = REPO / relative
        if not expected.is_file():
            problems.append((relative, ["<staged file is not in the baseline>"]))
        elif not actual.is_file():
            problems.append((relative, ["<staged delete of a scaffold file>"]))
        elif expected.read_bytes() != actual.read_bytes():
            problems.append((relative, diff_excerpt(actual, expected)))
    if problems:
        for relative, lines in problems:
            print(f"::error file={relative}::scaffold drift", file=sys.stderr)
            for line in lines:
                print(line, file=sys.stderr)
        return fail(f"{len(problems)} staged scaffold file(s) differ")
    print(f"scaffold staged check: {len(staged)} file(s) match v{tag}")
    return 0


def main():
    """Run the full tree check, or the offline staged check for pre-commit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="compare only staged scaffold files")
    args = parser.parse_args()
    metadata = load_metadata()
    if metadata.get("name") == "fcpp":
        print("upstream repository: scaffold check skipped")
        return 0
    tag = str(metadata.get("scaffold", "")).strip()
    if not tag:
        return fail("metadata.scaffold is missing")
    if args.staged:
        baseline = fallback_baseline(tag)
        if baseline is None:
            print(f"::notice::no scaffold-baseline for v{tag}; staged check skipped")
            return 0
        return check_staged(load_patterns(baseline), tag)
    with tempfile.TemporaryDirectory() as workdir:
        try:
            upstream = fetch_upstream(tag, workdir)
        except RuntimeError as exc:
            upstream = fallback_baseline(tag)
            if upstream is None:
                return fail(f"cannot fetch v{tag}: {exc}")
            print(f"::notice::upstream fetch failed; using scaffold-baseline for v{tag}")
        patterns = load_patterns(upstream)
        problems, checked_upstream = compare_tree(upstream, patterns)
        problems += check_tolerated(upstream, checked_upstream, workdir)
        if problems:
            for relative, lines in problems:
                print(f"::error file={relative}::scaffold drift", file=sys.stderr)
                for line in lines:
                    print(line, file=sys.stderr)
            return fail(f"{len(problems)} path(s) differ from v{tag}")
    print(f"scaffold check: {len(checked_upstream)} file(s) match v{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
