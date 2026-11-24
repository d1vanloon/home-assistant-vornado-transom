from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MANIFEST = Path("custom_components/vornado_transom/manifest.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", default=None)
    args = parser.parse_args(argv)
    try:
        release(parse_part(args.part))
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def release(part: str) -> None:
    actor = required("GITHUB_ACTOR")
    email = f"{required('GITHUB_ACTOR_ID')}+{actor}@users.noreply.github.com"
    command(["git", "fetch", "origin", "master"])
    command(["git", "checkout", "-B", "master", "origin/master"])
    text = MANIFEST.read_text(encoding="utf-8")
    current = read_version(text)
    target = format_version(bump(parse_version(current), part))
    tag = f"v{target}"
    if remote_tag(tag):
        raise RuntimeError(f"{tag} already exists")
    message = f"Bump version to {target} in manifest.json"
    MANIFEST.write_bytes(replace_version(text, current, target).encode("utf-8"))
    command(["git", "add", "--", MANIFEST.as_posix()])
    command(
        ["git", "commit", "-m", message, "--", MANIFEST.as_posix()],
        extra={
            "GIT_AUTHOR_NAME": actor,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": actor,
            "GIT_COMMITTER_EMAIL": email,
        },
    )
    command(["git", "push", "origin", "HEAD:refs/heads/master"])
    sha = command(["git", "rev-parse", "HEAD"]).stdout.decode("utf-8").strip()
    command(["git", "tag", tag, sha])
    command(["git", "push", "origin", f"refs/tags/{tag}"])
    command(["gh", "workflow", "run", "release.yaml", "--ref", tag])
    print(f"Started the release for {tag}")


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
    return tuple(int(piece) for piece in pieces)


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
    return ".".join(str(piece) for piece in version)


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


def remote_tag(name: str) -> bool:
    listed = command(["git", "ls-remote", "origin", f"refs/tags/{name}"])
    return bool(listed.stdout.strip())


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing")
    return value


def command(
    args: list[str],
    *,
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    proc_env = os.environ.copy()
    if extra:
        proc_env.update(extra)
    proc = subprocess.run(args, check=False, capture_output=True, env=proc_env)
    if proc.returncode != 0:
        detail = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"{args[0]} failed")
    return proc


if __name__ == "__main__":
    raise SystemExit(main())
