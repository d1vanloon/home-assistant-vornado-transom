from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MANIFEST = "custom_components/vornado_transom/manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", default=None)
    args = parser.parse_args(argv)
    try:
        run(parse_part(args.part))
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def run(part: str) -> None:
    repository = env("GITHUB_REPOSITORY")
    env("GITHUB_ACTOR")
    env("GITHUB_ACTOR_ID")
    git(["fetch", "origin", "master", "--tags"])
    current_text = blob("origin/master")
    current = read_version(current_text)
    target = format_version(bump(parse_version(current), part))
    tag = f"v{target}"
    if tag_exists(tag):
        raise RuntimeError(f"{tag} already exists")
    message = f"Bump version to {target} in manifest.json"
    branch = f"version-bump/{tag}"
    number, head = ensure_pull_request(branch, current_text, current, target, message)
    merge_sha = merge_pull_request(repository, number, head, message)
    git(["fetch", "origin", "master"])
    if rev_parse("origin/master") != merge_sha:
        raise RuntimeError("merged commit is not master")
    if f'"version": "{target}"' not in blob(merge_sha):
        raise RuntimeError("merged manifest version does not match")
    git(["tag", tag, merge_sha])
    git(["push", "origin", f"refs/tags/{tag}"])
    gh(["workflow", "run", "release.yaml", "--ref", tag])
    print(f"Started the release for {tag}")


def ensure_pull_request(
    branch: str,
    current_text: str,
    current: str,
    target: str,
    message: str,
) -> tuple[int, str]:
    publish_branch(branch, current_text, current, target, message)
    existing = open_pull_request(branch)
    if existing is not None:
        return existing
    created = gh(
        [
            "pr",
            "create",
            "--base",
            "master",
            "--head",
            branch,
            "--title",
            message,
            "--body",
            message,
        ],
        check=False,
    )
    if created.returncode != 0 and "already exists" not in combined(created).lower():
        raise RuntimeError(combined(created).strip() or "pull request create failed")
    existing = open_pull_request(branch)
    if existing is None:
        raise RuntimeError("pull request was not created")
    return existing


def publish_branch(
    branch: str,
    current_text: str,
    current: str,
    target: str,
    message: str,
) -> None:
    if remote_sha(f"refs/heads/{branch}") is None:
        git(["checkout", "-B", branch, "origin/master"])
        Path(MANIFEST).write_bytes(replace_version(current_text, current, target).encode("utf-8"))
        git(["add", "--", MANIFEST])
        commit(message)
        git(["push", "origin", f"HEAD:refs/heads/{branch}"])
        return
    git(["fetch", "origin", branch])
    if f'"version": "{target}"' not in blob(f"origin/{branch}"):
        raise RuntimeError(f"{branch} does not set the version to {target}")


def open_pull_request(branch: str) -> tuple[int, str] | None:
    owner = env("GITHUB_REPOSITORY").split("/", 1)[0]
    payload = json.loads(
        gh(
            [
                "pr",
                "list",
                "--head",
                f"{owner}:{branch}",
                "--base",
                "master",
                "--state",
                "open",
                "--json",
                "number,headRefOid",
            ]
        ).stdout.decode("utf-8")
        or "[]"
    )
    if not payload:
        return None
    pull_request = payload[0]
    return int(pull_request["number"]), str(pull_request["headRefOid"])


def merge_pull_request(repository: str, number: int, head: str, message: str) -> str:
    merged = gh(
        [
            "api",
            "--method",
            "PUT",
            f"repos/{repository}/pulls/{number}/merge",
            "-f",
            f"sha={head}",
            "-f",
            f"commit_title={message}",
            "-f",
            "merge_method=squash",
            "--jq",
            ".sha",
        ],
        check=False,
    )
    if merged.returncode == 0 and merged.stdout.decode("utf-8").strip():
        return merged.stdout.decode("utf-8").strip()
    view = json.loads(
        gh(["pr", "view", str(number), "--json", "state,mergeCommit"]).stdout.decode("utf-8")
    )
    merge_commit = view.get("mergeCommit") or {}
    sha = merge_commit.get("oid")
    if str(view.get("state", "")).upper() == "MERGED" and sha:
        return str(sha)
    raise RuntimeError(combined(merged).strip() or "squash merge failed")


def commit(message: str) -> None:
    actor = env("GITHUB_ACTOR")
    email = f"{env('GITHUB_ACTOR_ID')}+{actor}@users.noreply.github.com"
    git(
        ["commit", "-m", message, "--", MANIFEST],
        extra={
            "GIT_AUTHOR_NAME": actor,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": actor,
            "GIT_COMMITTER_EMAIL": email,
        },
    )


def parse_part(text: str | None) -> str:
    part = (text or "").strip().lower()
    if part == "":
        return "minor"
    if part not in {"major", "minor", "patch"}:
        raise ValueError(f"unknown part {text}")
    return part


def parse_version(text: str) -> tuple[int, int, int]:
    pieces = text.split(".")
    if len(pieces) != 3 or any(not is_component(piece) for piece in pieces):
        raise ValueError(f"version {text} is not X.Y.Z")
    major, minor, patch = (int(piece) for piece in pieces)
    return major, minor, patch


def is_component(text: str) -> bool:
    if text == "0":
        return True
    return len(text) > 0 and not text.startswith("0") and text.isdigit()


def bump(version: tuple[int, int, int], part: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if part == "major":
        return major + 1, 0, 0
    if part == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def format_version(version: tuple[int, int, int]) -> str:
    major, minor, patch = version
    return f"{major}.{minor}.{patch}"


def read_version(text: str) -> str:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("manifest does not parse") from exc
    if not isinstance(document, dict):
        raise ValueError("manifest does not parse")
    value = document.get("version")
    if not isinstance(value, str):
        raise ValueError("manifest version is missing")
    parse_version(value)
    return value


def replace_version(text: str, current: str, target: str) -> str:
    old = f'"version": "{current}"'
    new = f'"version": "{target}"'
    if text.count(old) != 1:
        raise ValueError("version line is missing or repeated")
    return text.replace(old, new, 1)


def tag_exists(name: str) -> bool:
    return remote_sha(f"refs/tags/{name}") is not None


def remote_sha(ref: str) -> str | None:
    text = git(["ls-remote", "origin", ref]).stdout.decode("utf-8").strip()
    if not text:
        return None
    return text.split()[0]


def blob(rev: str) -> str:
    return git(["show", f"{rev}:{MANIFEST}"]).stdout.decode("utf-8")


def rev_parse(rev: str) -> str:
    return git(["rev-parse", rev]).stdout.decode("utf-8").strip()


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing")
    return value


def git(args: list[str], *, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    return command(["git", *args], extra=extra)


def gh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return command(["gh", *args], extra={"GH_REPO": env("GITHUB_REPOSITORY")}, check=check)


def command(
    args: list[str],
    *,
    extra: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    proc_env = os.environ.copy()
    if extra:
        proc_env.update(extra)
    proc = subprocess.run(args, check=False, capture_output=True, env=proc_env)
    if check and proc.returncode != 0:
        raise RuntimeError(combined(proc).strip() or f"{args[0]} failed")
    return proc


def combined(proc: subprocess.CompletedProcess[bytes]) -> str:
    return (proc.stdout + proc.stderr).decode("utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
