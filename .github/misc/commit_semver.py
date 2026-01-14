import json
import subprocess
import sys
import re
from pathlib import Path
from datetime import date


def compact_json(obj):
    json_str = json.dumps(obj, indent=2, ensure_ascii=False)

    def compress_array(match):
        content = match.group(1)
        compressed = re.sub(r'\s+', ' ', content).strip()
        return f"[{compressed}]"

    def compress_object(match):
        content = match.group(1)
        compressed = re.sub(r'\s+', ' ', content).strip()
        return f"{{{compressed}}}"

    json_str = re.sub(r'\[\s*((?:"[^"]*"|[^[\]{}"])*)\s*\]', compress_array, json_str)
    json_str = re.sub(r'{\s*((?:"[^"]*"\s*:\s*(?:"[^"]*"|[^,{}]*))*)\s*}', compress_object, json_str)

    return json_str


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def get_repo_url():
    try:
        result = run("git remote get-url origin")
        remote_url = result.stdout.strip()

        if not remote_url:
            print("Warning: Could not get git remote URL, using placeholder")
            return "https://github.com/owner/repo"

        if remote_url.endswith(".git"):
            remote_url = remote_url[:-4]

        if remote_url.startswith("git@"):
            remote_url = remote_url[4:]
            remote_url = remote_url.replace(":", "/", 1)
            remote_url = "https://" + remote_url

        parts = remote_url.rstrip("/").split("/")

        if len(parts) >= 2:
            owner = parts[-2]
            repo = parts[-1]
            return f"https://github.com/{owner}/{repo}"

        return remote_url

    except Exception as e:
        print(f"Warning: Error getting repository URL: {e}")
        return "https://github.com/owner/repo"


def analyze_commits():
    repo_url = get_repo_url()

    result = run("git log --grep='chore(release):' -n 1 --format=%H")
    since = result.stdout.strip()

    format_str = "%s|@|%H---ENDMSG---"
    if since:
        cmd = f"git log {since}..HEAD --format='{format_str}'"
    else:
        cmd = f"git log --format='{format_str}'"

    result = run(cmd)
    if not result.stdout.strip():
        return 0, {}

    raw_blocks = result.stdout.strip().split("---ENDMSG---\n")
    raw_blocks = [_.split('|@|') for _ in raw_blocks]
    clean_blocks = [[_[0].split('\n')[0], _[1] if "---ENDMSG---" not in _[1] else _[1][:-12]] for _ in raw_blocks]

    level = 0
    entries = {
        "Breaking Changes": [],
        "Features": [],
        "Bug Fixes": [],
        "Performance Improvements": [],
        "Documentation": [],
        "Tests": [],
        "Build System": [],
        "CI": [],
        "Refactoring": [],
        "Style": [],
        "Chore": [],
        "Other": []
    }

    type_mapping = {
        "feat": ("Features", 2),
        "fix": ("Bug Fixes", 1),
        "perf": ("Performance Improvements", 1),
        "docs": ("Documentation", 0),
        "test": ("Tests", 0),
        "build": ("Build System", 0),
        "ci": ("CI", 0),
        "refactor": ("Refactoring", 0),
        "style": ("Style", 0),
        "chore": ("Chore", 0)
    }

    for (full_msg, _), (first_line, current_hash) in zip(raw_blocks, clean_blocks):
        clean_line = re.sub(r'^[a-z]+(\([^)]*\))?!?:\s*', '', first_line, flags=re.I)
        current_msg_level, category = 0, None

        has_breaking_change = "BREAKING CHANGE" in full_msg
        has_exclamation = bool(re.match(r"^[a-z]+(\([^)]*\))!:", first_line))
        type_match = re.match(r"^([a-z]+)(\([^)]*\))?!?:", first_line, flags=re.I)

        if type_match:
            commit_type = type_match.group(1).lower()

            if commit_type in type_mapping:
                category, base_level = type_mapping[commit_type]

                if has_breaking_change or has_exclamation:
                    current_msg_level = 3
                    if category != "Breaking Changes":
                        category = "Breaking Changes"
                else:
                    current_msg_level = base_level

        elif has_breaking_change or has_exclamation:
            current_msg_level = 3
            category = "Breaking Changes"

        if category and (current_msg_level > 0 or category == "Breaking Changes"):
            if category in set(entries.keys()).difference({"Other", }):
                level = max(level, current_msg_level)
                entry = f"* {clean_line} ([{current_hash[:7]}]({repo_url}/commit/{current_hash}))"
                entries[category].append(entry)
            else:
                entries["Other"].append(entry)

    return level, entries


def main():
    script_dir = Path(__file__).parent
    root_dir = script_dir.parent.parent
    metadata_path = root_dir / "metadata.json"
    changelog_path = root_dir / "Versioning.md"

    if not metadata_path.exists():
        print(f"Error: {metadata_path} not found")
        sys.exit(1)

    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    v_curr = metadata["version"]
    major, minor, patch = map(int, v_curr.split("."))

    bump_level, log_entries = analyze_commits()

    if bump_level == 0:
        print("No relevant changes detected. Skipping release.")
        sys.exit(0)

    if bump_level == 3:
        new_version = f"{major + 1}.0.0"
    elif bump_level == 2:
        new_version = f"{major}.{minor + 1}.0"
    else:
        new_version = f"{major}.{minor}.{patch + 1}"

    print(f"Bumping {v_curr} -> {new_version} (Level {bump_level})")

    metadata["version"] = new_version
    with open(metadata_path, "w", encoding="utf-8") as f:
        json_str = compact_json(metadata)
        f.write(json_str)
        f.write("\n")

    if log_entries:
        new_log = "#" * max(1, 3 - bump_level) + f" {new_version} ({date.today().isoformat()})\n\n"
        for category, items in log_entries.items():
            if items:
                new_log += f"### {category}\n"
                new_log += "\n".join(items) + "\n\n"

        content = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else ""
        changelog_path.write_text(new_log + content, encoding="utf-8")

    run("git config user.name 'github-actions'")
    run("git config user.email 'github-actions@github.com'")
    run(f"git add {metadata_path} {changelog_path}")
    run(f'git commit -m "chore(release): {new_version} [skip ci]"')


if __name__ == "__main__":
    main()
