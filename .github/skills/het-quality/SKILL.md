---
name: het-quality
description: 'Quality & security gates for the fcpp template (MegaLinter, clang-tidy, clang-format, gitleaks). Use when: code fails lint / clang-tidy / clang-format / security scan, or quality CI is red. 质量/安全门禁：本地自查 + MegaLinter。'
argument-hint: "Scope (optional) / 检查范围（可选）"
user-invocable: true
---

# S4 · het-quality — Quality / Security Gates（质量与安全门禁）

> Facts: `.github/skills/_shared/code-conventions.md`、`.mega-linter.yml`、`.github/misc/.clang-tidy`、`.github/misc/.clang-format`。

## Mental Model（心智模型）

> "MegaLinter runs on `:shield:` (or any PR): clang-tidy/clang-format for C/C++, gitleaks for secrets, SAST (semgrep/checkov/devskim). **Warning = failure** (WarningsAsErrors), so lint locally first."
> 提交带 `:shield:`（或任何 PR）跑质量与安全扫描；warning 即失败，最好本地先自查。

## 3-Step Checklist（三步操作清单）

1. Lint locally（本地自查）:
   ```bash
   clang-format --dry-run --Werror include/ src/
   clang-tidy src/*.cpp -- -std=c++17
   ```
2. Commit with `ci(:shield:): ...` (or open a PR — security scan runs on every PR).
3. Actions → `MegaLinter` workflow → report in `megalinter-reports` artifact.

## What Is Checked（检查内容一览）

| Check（检查） | Tool（工具） | Scope（范围） |
|------|------|------|
| Format | clang-format (C right-aligned / C++ left-aligned, 120 cols) | include/ + src/ |
| Static analysis | clang-tidy (bugprone/performance, WarningsAsErrors) | .h/.c/.hpp/.cpp |
| Secrets | gitleaks (allowlist `.github/misc/.gitleaks.toml`) | repo (infra exempt) |
| SAST | semgrep / checkov / devskim | include/ + src/ + infra |

## Self-Help When Red（失败自救）

1. Format: `clang-format -i <file>` auto-fix. 格式问题直接自动修。
2. clang-tidy warning: fix per hints (cannot silence). 按提示改代码。
3. gitleaks: check you did not commit a secret; infra files are exempt. 检查是否误提交密钥。
4. Report location: `MegaLinter reports` artifact. 报告在 Artifacts。
5. Deep troubleshooting → `het-fix-ci` (S6).

