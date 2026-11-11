#!/usr/bin/env python3
"""Resolve the on-board selection declared in metadata.json into a temporary build matrix.

Three owners, kept apart on purpose:

* ``metadata.json`` says **what to build** (``os`` / ``profile_arch`` / ``toolchain_version``).
  ``profile_arch`` is the physical CPU core (``cortex-a53``), deliberately *not* Conan's
  ``settings.arch`` (which cannot tell a53 from a55 from a76).
* ``supportable-board-matrix.yml`` says **what the self-hosted runner has proven** on hardware.
* The platform catalog (``board_ci_target_catalog.json``) says **what each target is**
  (ABI, FPU, route, remote path). This script never invents or copies those facts; it only
  selects the verified blocks and hands them back.

The output is ``tmp-build-matrix.yml``: byte-identical for identical inputs, written under
``$RUNNER_TEMP`` and never committed. The platform expander consumes it unchanged.

Exit codes: 0 resolved (warnings allowed), 1 the selection cannot be honoured,
2 bad arguments or unreadable/invalid files.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

# (os, physical core) -> the verified target that owns it. A sibling is another target the
# same combination would also match; it is reported, never selected silently.
TARGET_MAP: dict[tuple[str, str], dict[str, Any]] = {
    ("Linux", "cortex-a7"): {"id": "linux-myir-3506-A7", "conan_arch": "armv7"},
    ("Linux", "cortex-a55"): {"id": "linux-orangepi-rk3566-A55", "conan_arch": "armv8"},
    ("Linux", "cortex-a76"): {
        "id": "linux-orangepi-rk3588-A76",
        "conan_arch": "armv8",
        "siblings": ["linux-raspberry-bcm2712-A76"],
    },
    ("Linux", "cortex-a53"): {"id": "linux-orangepi-h6-A53", "conan_arch": "armv8"},
    ("Linux", "x86_64"): {"id": "linux-x86_64", "conan_arch": "x86_64"},
    ("baremetal", "cortex-m0"): {"id": "baremetal-cortex-m0", "conan_arch": "cortex-m0"},
    ("baremetal", "cortex-m3"): {"id": "baremetal-cortex-m3", "conan_arch": "cortex-m3"},
    ("baremetal", "cortex-m4"): {"id": "baremetal-cortex-m4", "conan_arch": "cortex-m4"},
    ("baremetal", "cortex-m7"): {"id": "baremetal-cortex-m7", "conan_arch": "cortex-m7d"},
    ("baremetal", "cortex-m23"): {"id": "baremetal-cortex-m23", "conan_arch": "armv8_32"},
    ("baremetal", "cortex-m33"): {"id": "baremetal-cortex-m33", "conan_arch": "armv8_32"},
}

# Conan arch families that cannot identify a core. Writing one of these in
# on_board_validation.profile_arch is a configuration error, not a zero-match.
CONAN_ARCH_ONLY = frozenset({"armv6", "armv7", "armv7hf", "armv8_32", "armv8", "armv8.3"})

# Every core the platform has ever registered. A core outside this set is a typo or an
# unregistered board (error); a known core under the wrong OS is merely a rare combination
# (warning), because on_board_validation is written as an os x arch cross product.
KNOWN_CORES = frozenset(arch for _, arch in TARGET_MAP)

VERSION_ALIASES = {"11.3": "11.3.rel1"}

DIMENSIONS = ("os", "profile_arch", "toolchain_version")


class UsageError(Exception):
    """Bad arguments or unreadable/invalid input (exit 2)."""


class ResolutionError(Exception):
    """The selection is well-formed but cannot be turned into a matrix (exit 1)."""


def _annotate(level: str, message: str, enabled: bool) -> None:
    prefix = {"error": "::error::", "warning": "::warning::", "notice": "::notice::"}[level]
    stream = sys.stderr if level == "error" else sys.stdout
    print(f"{prefix}{message}" if enabled else message, file=stream, flush=True)


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise UsageError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError(f"{path}: root must be a mapping")
    return data


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise UsageError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError(f"{path}: root must be an object")
    return data


def _normalize_version(value: str) -> str:
    return VERSION_ALIASES.get(value, value)


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise UsageError(f"on_board_validation.{field} must be a non-empty array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise UsageError(f"on_board_validation.{field} must contain non-empty strings")
        item = item.strip()
        if item in result:
            raise UsageError(f"on_board_validation.{field} contains duplicate {item!r}")
        result.append(item)
    return result


def validate_selection(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        raise UsageError("on_board_validation must be an object")
    unknown = sorted(set(raw) - set(DIMENSIONS))
    if unknown:
        raise UsageError(f"on_board_validation has unknown key(s): {', '.join(unknown)}")
    missing = [name for name in DIMENSIONS if name not in raw]
    if missing:
        raise UsageError(f"on_board_validation is missing {', '.join(missing)}")
    selection = {name: _string_list(raw[name], name) for name in DIMENSIONS}
    selection["toolchain_version"] = [
        _normalize_version(version) for version in selection["toolchain_version"]
    ]
    return selection


def load_gold(path: Path) -> tuple[dict, dict[str, dict]]:
    data = _load_yaml(path)
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise UsageError(f"{path}: targets must be a non-empty list")
    by_id: dict[str, dict] = {}
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise UsageError(f"{path}: targets[{index}] must be a mapping")
        target_id = target.get("id")
        if not isinstance(target_id, str) or not target_id:
            raise UsageError(f"{path}: targets[{index}] has no id")
        if target_id in by_id:
            raise UsageError(f"{path}: duplicate target id {target_id!r}")
        versions = target.get("toolchain_versions")
        if not isinstance(versions, list) or not versions:
            raise UsageError(f"{path}: {target_id} has no toolchain_versions")
        if not isinstance(target.get("benchmark"), dict):
            raise UsageError(f"{path}: {target_id} has no benchmark block")
        by_id[target_id] = target
    return data, by_id


def resolve(selection: dict[str, list[str]], gold: dict[str, dict]) -> tuple[dict[str, list[str]], list[str], list[dict]]:
    """Map the cartesian product of the selection onto verified target ids."""
    selected: dict[str, list[str]] = {}
    warnings: list[str] = []
    resolved: list[dict] = []
    verified_any = False

    for os_name in selection["os"]:
        for arch in selection["profile_arch"]:
            if arch in CONAN_ARCH_ONLY:
                raise UsageError(
                    f"profile_arch {arch!r} is a Conan arch family; write the CPU core "
                    f"(for example cortex-a53) so sibling SoCs stay distinguishable"
                )
            entry = TARGET_MAP.get((os_name, arch))
            if entry is None:
                if arch not in KNOWN_CORES:
                    raise ResolutionError(
                        f"{os_name}/{arch}: not registered for on-board CI; add it to the platform "
                        f"catalog and to TARGET_MAP before selecting it"
                    )
                warnings.append(
                    f"{os_name}/{arch}: no such board (the core is registered, the OS is not); "
                    f"combination ignored"
                )
                continue
            target_id = entry["id"]
            if target_id not in gold:
                raise ResolutionError(
                    f"{os_name}/{arch} maps to {target_id}, which is absent from the gold standard"
                )
            if entry.get("siblings"):
                warnings.append(
                    f"{os_name}/{arch} also matches {', '.join(entry['siblings'])}; "
                    f"{target_id} wins by table preference"
                )
            versions = gold[target_id]["toolchain_versions"]
            for version in selection["toolchain_version"]:
                verified = version in versions
                resolved.append(
                    {
                        "os": os_name,
                        "profile_arch": arch,
                        "toolchain_version": version,
                        "id": target_id,
                        "verified": verified,
                    }
                )
                if not verified:
                    warnings.append(
                        f"{os_name}/{arch}/{version}: no hardware evidence for {target_id} "
                        f"(verified: {', '.join(versions)})"
                    )
                    continue
                verified_any = True
                bucket = selected.setdefault(target_id, [])
                if version not in bucket:
                    bucket.append(version)

    if not verified_any:
        raise ResolutionError("no requested combination is hardware-verified")
    return selected, warnings, resolved


def package_ref(metadata: dict) -> str:
    name = metadata.get("name")
    version = metadata.get("version")
    if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
        raise UsageError("metadata.json must define non-empty name and version")
    return f"{name}/{version}"


def build_matrix(gold: dict, ref: str, selected: dict[str, list[str]]) -> dict:
    targets = []
    for target in gold["targets"]:
        target_id = target["id"]
        if target_id not in selected:
            continue
        targets.append(
            {
                "id": target_id,
                "target_key": target["target_key"],
                "build_kind": target["build_kind"],
                "os": target["os"],
                "arch": target["arch"],
                "enabled": True,
                "toolchain_versions": list(selected[target_id]),
                "benchmark": target["benchmark"],
            }
        )
    return {"package_ref": ref, "defaults": gold["defaults"], "targets": targets}


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow, False)


def dump_matrix(matrix: dict) -> str:
    text = yaml.dump(
        matrix,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=1000,
    )
    return text.replace(": ''", ': ""')


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_report(path: Path | None, report: dict) -> None:
    if path is None:
        return
    write_atomic(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def _selection_report(selection: dict | None, ids: list[str] | None, resolved: list[dict], selected: dict, warnings: list[str]) -> dict:
    return {
        "selection": selection,
        "ids": ids,
        "resolved": resolved,
        "selected_targets": list(selected),
        "warnings": warnings,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve metadata.json board selection into a temporary matrix.")
    parser.add_argument("--metadata", default=str(ROOT / "metadata.json"))
    parser.add_argument("--supportable", default=str(ROOT / ".github/misc/supportable-board-matrix.yml"))
    parser.add_argument("--out", help="where to write tmp-build-matrix.yml")
    parser.add_argument("--report", help="optional JSON resolution report")
    parser.add_argument("--mode", choices=("selection", "all", "ids"), default="selection")
    parser.add_argument("--ids", default="", help="comma-separated ids for --mode ids")
    parser.add_argument("--dry-run", action="store_true", help="resolve and report, write nothing")
    parser.add_argument("--annotations", action="store_true", help="emit GitHub workflow commands")
    args = parser.parse_args(argv)
    if args.mode == "ids" and not [item for item in args.ids.split(",") if item.strip()]:
        parser.error("--mode ids requires --ids")
    if not args.dry_run and not args.out:
        parser.error("--out is required unless --dry-run is set")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report_path = Path(args.report) if args.report else None
    selection: dict | None = None
    ids_input: list[str] | None = None
    try:
        metadata = _load_json(Path(args.metadata))
        gold, by_id = load_gold(Path(args.supportable))
        ref = package_ref(metadata)

        resolved: list[dict] = []
        warnings: list[str] = []

        if args.mode == "selection":
            triggers = metadata.get("workflow_triggers")
            if not isinstance(triggers, dict) or triggers.get("on_board") is not True:
                _annotate("notice", "on-board CI is disabled by metadata.json (workflow_triggers.on_board)", args.annotations)
                _write_report(report_path, {"mode": args.mode, "selected_targets": [], "warnings": []})
                return 0
            selection = validate_selection(metadata.get("on_board_validation"))
            selected, warnings, resolved = resolve(selection, by_id)
        elif args.mode == "all":
            selected = {target["id"]: list(target["toolchain_versions"]) for target in gold["targets"]}
            ids_input = list(selected)
        else:
            requested = [item.strip() for item in args.ids.split(",") if item.strip()]
            ids_input = requested
            unknown = [item for item in requested if item not in by_id]
            if unknown:
                raise ResolutionError(f"ids not present in the gold standard: {', '.join(unknown)}")
            selected = {item: list(by_id[item]["toolchain_versions"]) for item in requested}

        matrix = build_matrix(gold, ref, selected)
        for message in warnings:
            _annotate("warning", message, args.annotations)
        _annotate(
            "notice",
            f"on-board selection resolved to {len(matrix['targets'])} target(s): "
            + ", ".join(selected),
            args.annotations,
        )
        _write_report(
            report_path,
            {
                "mode": args.mode,
                "package_ref": ref,
                **_selection_report(selection, ids_input, resolved, selected, warnings),
            },
        )
        if args.dry_run:
            print(f"dry-run: would write {len(matrix['targets'])} target(s) for {ref}")
            return 0
        text = dump_matrix(matrix)
        write_atomic(Path(args.out), text)
        print(f"wrote {args.out} ({len(matrix['targets'])} target(s) for {ref})")
        return 0
    except UsageError as exc:
        _annotate("error", str(exc), args.annotations)
        _write_report(report_path, {"mode": args.mode, "error": str(exc), "selection": selection, "ids": ids_input})
        return 2
    except ResolutionError as exc:
        _annotate("error", str(exc), args.annotations)
        _write_report(report_path, {"mode": args.mode, "error": str(exc), "selection": selection, "ids": ids_input})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
