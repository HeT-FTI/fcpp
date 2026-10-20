# Harness Adapters（跨 Harness 调用矩阵）

> Fact source for "how does this harness see the skills?". Canonical skills are **only** in
> `.github/skills/`; every other root is a generated view. 唯一正文在 `.github/skills/`，其余根目录都是生成视图。
>
> **Root hygiene（根目录最小化）**: no root-level `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` /
> `copilot-instructions.md`. Default `sync` creates exactly one root directory, `.agents/`.
> Claude Code / Codex views are optional, created only when asked. 根目录默认只多一个 `.agents/` 目录。

## Canonical & Views（规范路径与视图）

| Path | Kind | Read by | Committed? |
|---|---|---|---|
| `.github/skills/` | **canonical** | Copilot (native), everyone via direct read | yes |
| `.agents/skills` | link → `.github/skills` | DSH, Cursor, Gemini CLI, Copilot (VS 2026) | no (git-ignored) |
| `.claude/skills` | optional link → `.github/skills` | Claude Code | no (git-ignored) |
| `.codex/skills` | optional link → `.github/skills` | Codex | no (git-ignored) |

Default (one new directory):

```bash
python .github/skills/_shared/skillkit/cli.py sync
```

Optional harness views:

```bash
python .github/skills/_shared/skillkit/cli.py sync --harness claude   # .claude/skills
python .github/skills/_shared/skillkit/cli.py sync --harness codex    # .codex/skills
python .github/skills/_shared/skillkit/cli.py sync --harness all      # all three
```

Validate / inspect:

```bash
python .github/skills/_shared/skillkit/cli.py sync --check
python .github/skills/_shared/skillkit/cli.py validate --strict
python .github/skills/_shared/skillkit/cli.py status
python .github/skills/_shared/skillkit/cli.py catalog --format markdown
```

Do **not** create `.dsh/skills`, `.cursor/skills`, or `.gemini/skills` by hand: DSH/Cursor/Gemini already
read `.agents/skills`, and a second root would surface the same skill twice. 不要手建 `.dsh`/`.cursor`/`.gemini`。

## Invocation Matrix（调用方式）

| Harness | Discovery root | Manual invocation | Automatic? |
|---|---|---|---|
| **DSH** | `.agents/skills` (rank 200) | `skill` tool with the exact name; `/het-build` gesture | yes, catalog at pre-step |
| **Claude Code** | `.claude/skills` (optional view) | `/het-build` | yes, by description |
| **Codex** | `.codex/skills` (optional view); `$CODEX_HOME/skills` user root | `/use het-build` | yes, by description |
| **Cursor** | `.agents/skills` (also `.claude`, `.codex` if present) | `/het-build` or `@het-build` | yes |
| **GitHub Copilot** | `.github/skills` (VS 2026 also `.claude`/`.agents`) | `/het-build` in chat | yes, by description |
| **Gemini CLI** | `.agents/skills` alias (priority over `.gemini/skills`) | `activate_skill` | yes |
| **Generic / other** | direct file read | read `.github/skills/<name>/SKILL.md` | depends on the harness |

Sources（2026-10）：

- DSH: built-in `@deepseek-ai/dsh-skill-filesystem` / `dsh-tool-skill` package docs.
- Claude Code: <https://code.claude.com/docs/en/skills>
- Cursor: <https://cursor.com/docs/skills>
- Copilot: <https://docs.github.com/zh/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills>
- Visual Studio 2026: <https://learn.microsoft.com/en-us/visualstudio/ide/copilot-agent-skills>
- Gemini CLI: <https://geminicli.com/docs/cli/skills/>
- Open standard: <https://agentskills.io/specification>

## Fallback（没有原生技能加载器时）

1. No root-level agent file is required; start from the canonical tree. 不依赖根级入口文件。
2. Load `het-agent` for routing, or `het-guide` for onboarding:
   - `.github/skills/het-agent/SKILL.md`
   - `.github/skills/het-guide/SKILL.md`
3. Read `.github/skills/_shared/routing.md` for the intent → skill table.
4. Read the selected `.github/skills/<name>/SKILL.md` and follow it.
5. Print a catalog for the model when it cannot discover files:

```bash
python .github/skills/_shared/skillkit/cli.py catalog --format markdown
```

## Single-Body Rule（唯一正文规则）

- A view must be a **link** (symlink or Windows junction), never a copied tree.
- `het-agent` exists only at `.github/skills/het-agent/SKILL.md`; a second real `het-agent/SKILL.md`
  anywhere else is a contract violation.
- Edit the canonical file, not the view. 改 canonical，不改视图。
- `skillkit validate` fails on a real directory or duplicated `SKILL.md` under a harness root.

## Shell Portability（命令可移植性）

The skills show POSIX commands (`find`, `clang-format`, `python ./docs/build.py`). On Windows use one of:

- **WSL** or **Git Bash** for the POSIX commands;
- the repository's `.devcontainer/` (recommended for a reproducible Linux toolchain);
- the equivalent editor task/command, or PowerShell (e.g. `Get-ChildItem` instead of `find`).

The Python and Conan commands (`python .github/skills/_shared/skillkit/cli.py ...`, `conan create ...`) are cross-platform.
