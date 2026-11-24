from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from enum import Enum
from pathlib import Path

from tools.version_bump.bump import (
    MANIFEST_PATH,
    CreateTag,
    Decision,
    DispatchRelease,
    Done,
    MergeBump,
    OpenBump,
    OpenBumpPr,
    Part,
    Refuse,
    ReleaseAttempt,
    Snapshot,
    TagState,
    Version,
    decide,
    manifest_version,
    parse_part,
    parse_version,
    set_manifest_version,
    tag_name,
)

_MAX_STEPS = 8
_BRANCH_PREFIX = "version-bump/v"


class _ManifestRead(Enum):
    MISSING = "missing"
    INVALID = "invalid"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.version_bump")
    parser.add_argument("--part", default=None)
    args = parser.parse_args(argv)
    try:
        part = parse_part(args.part)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return run(part)


def run(part: Part) -> int:
    run_id = os.environ.get("BUMP_RUN_ID", "").strip()
    if not run_id:
        print("BUMP_RUN_ID is missing", file=sys.stderr)
        return 1
    for _ in range(_MAX_STEPS):
        try:
            snapshot = observe(run_id)
            decision = decide(snapshot, part)
        except Exception as exc:
            print(_error_text(exc), file=sys.stderr)
            return 1
        if isinstance(decision, Done):
            print(decision.message)
            return 0
        if isinstance(decision, Refuse):
            print(decision.message, file=sys.stderr)
            return 1
        try:
            apply(decision, run_id)
        except Exception as exc:
            print(_error_text(exc), file=sys.stderr)
            return 1
        if isinstance(decision, DispatchRelease):
            print(f"Dispatched release for {tag_name(decision.version)}")
            return 0
    print("version bump did not finish", file=sys.stderr)
    return 1


def observe(run_id: str) -> Snapshot:
    _run(["git", "fetch", "origin", "master", "--tags"])
    master_sha = _text(["git", "rev-parse", "origin/master"]).strip()
    master_text = _blob_text("origin/master", MANIFEST_PATH)
    if master_text is None:
        raise RuntimeError("manifest does not parse")
    try:
        master_version = manifest_version(master_text)
    except ValueError as exc:
        raise RuntimeError("manifest does not parse") from exc
    tags = _load_tags()
    tag = tags.get(master_version)
    owned, open_bumps = _open_bumps(run_id)
    return Snapshot(
        master_sha=master_sha,
        master_version=master_version,
        introduce_sha=_introduce_sha(master_version),
        tags=tags,
        master_release=_release_attempt(master_version, None if tag is None else tag.sha),
        open_bumps=open_bumps,
        owned=owned,
    )


def apply(decision: Decision, run_id: str) -> None:
    if isinstance(decision, CreateTag):
        _apply_create_tag(decision)
        return
    if isinstance(decision, DispatchRelease):
        _apply_dispatch(decision)
        return
    if isinstance(decision, OpenBump):
        _apply_open_bump(decision, run_id)
        return
    if isinstance(decision, MergeBump):
        _apply_merge(decision, run_id)
        return
    raise RuntimeError(f"cannot apply {type(decision).__name__}")


def _apply_create_tag(decision: CreateTag) -> None:
    name = tag_name(decision.version)
    remote = _remote_tag_sha(name)
    if remote is not None:
        if remote != decision.sha:
            raise RuntimeError(f"tag {name} points at the wrong commit")
        return
    local = _local_tag_sha(name)
    if local is not None and local != decision.sha:
        raise RuntimeError(f"tag {name} points at the wrong commit")
    if local is None:
        _run(["git", "tag", name, decision.sha])
    _run(["git", "push", "origin", f"refs/tags/{name}"])


def _apply_dispatch(decision: DispatchRelease) -> None:
    _gh(["workflow", "run", "release.yaml", "--ref", tag_name(decision.version)])


def _apply_open_bump(decision: OpenBump, run_id: str) -> None:
    _run(["git", "fetch", "origin", "master"])
    master_text = _blob_text("origin/master", MANIFEST_PATH)
    if master_text is None:
        raise RuntimeError("manifest does not parse")
    try:
        current = manifest_version(master_text)
    except ValueError as exc:
        raise RuntimeError("manifest does not parse") from exc
    if current != decision.source:
        return
    branch = decision.branch
    if _remote_branch_sha(branch) is None:
        _run(["git", "checkout", "-B", branch, "origin/master"])
    else:
        _run(["git", "fetch", "origin", branch])
        _run(["git", "checkout", "-B", branch, f"origin/{branch}"])
        _require_branch_diff(master_text, decision.target)
    expected = set_manifest_version(master_text, decision.target)
    head_text = _blob_text("HEAD", MANIFEST_PATH)
    if head_text != expected:
        Path(MANIFEST_PATH).write_bytes(expected.encode("utf-8"))
        _run(["git", "add", "--", MANIFEST_PATH])
        _commit(decision.commit_message)
    _run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"])
    _open_pr(branch, decision.commit_message, run_id)


def _apply_merge(decision: MergeBump, run_id: str) -> None:
    _ensure_run_id(decision.pr_number, run_id)
    repo = _env("GITHUB_REPOSITORY")
    proc = _run(
        [
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{repo}/pulls/{decision.pr_number}/merge",
            "-f",
            f"sha={decision.expected_head_sha}",
            "-f",
            f"commit_title={decision.commit_message}",
            "-f",
            "merge_method=squash",
        ],
        check=False,
    )
    if proc.returncode == 0:
        return
    detail = _combined(proc)
    if "already merged" in detail.lower() or "already been merged" in detail.lower():
        return
    view = _gh(["pr", "view", str(decision.pr_number), "--json", "state"], check=False)
    if view.returncode == 0:
        payload = json.loads(view.stdout.decode("utf-8") or "{}")
        if str(payload.get("state", "")).lower() == "merged":
            return
    raise RuntimeError(detail.strip() or "squash merge failed")


def _introduce_sha(master_version: Version) -> str | None:
    listing = _text(["git", "rev-list", "--first-parent", "--parents", "origin/master"])
    cache: dict[str, Version | _ManifestRead] = {}

    def at(sha: str) -> Version | _ManifestRead:
        if sha not in cache:
            cache[sha] = _manifest_at(sha)
        return cache[sha]

    for line in listing.splitlines():
        parts = line.split()
        if not parts:
            continue
        sha = parts[0]
        parent = parts[1] if len(parts) > 1 else None
        parent_version: Version | _ManifestRead
        if parent is None:
            parent_version = _ManifestRead.MISSING
        else:
            parent_version = at(parent)
        if parent_version is _ManifestRead.INVALID:
            return None
        if at(sha) == master_version and parent_version != master_version:
            return sha
    return None


def _manifest_at(sha: str) -> Version | _ManifestRead:
    text = _blob_text(sha, MANIFEST_PATH)
    if text is None:
        return _ManifestRead.MISSING
    try:
        return manifest_version(text)
    except ValueError:
        return _ManifestRead.INVALID


def _load_tags() -> dict[Version, TagState]:
    plain: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in _text(["git", "ls-remote", "--tags", "origin"]).splitlines():
        if not line:
            continue
        sha, ref = line.split()
        if not ref.startswith("refs/tags/"):
            continue
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            plain[name] = sha
    tags: dict[Version, TagState] = {}
    for name, tag_sha in plain.items():
        if not name.startswith("v"):
            continue
        try:
            version = parse_version(name[1:])
        except ValueError:
            continue
        commit = peeled.get(name, tag_sha)
        text = _blob_text(commit, MANIFEST_PATH)
        if text is None:
            raise RuntimeError(f"tag {name} has no manifest")
        try:
            found = manifest_version(text)
        except ValueError as exc:
            raise RuntimeError(f"tag {name} manifest does not parse") from exc
        tags[version] = TagState(sha=commit, manifest_version=found)
    return tags


def _release_attempt(version: Version, tag_sha: str | None) -> ReleaseAttempt:
    if tag_sha is None:
        return ReleaseAttempt.NONE
    view = _gh(["release", "view", tag_name(version)], check=False)
    if view.returncode == 0:
        return ReleaseAttempt.PUBLISHED
    runs = _gh(
        [
            "run",
            "list",
            "--workflow",
            "release.yaml",
            "--commit",
            tag_sha,
            "--limit",
            "100",
            "--json",
            "status",
        ],
        check=False,
    )
    if runs.returncode != 0:
        raise RuntimeError(_combined(runs).strip() or "gh run list failed")
    payload = json.loads(runs.stdout.decode("utf-8").strip() or "[]")
    if not isinstance(payload, list):
        raise RuntimeError("gh run list returned unexpected output")
    for item in payload:
        if isinstance(item, dict) and item.get("status") in {"queued", "in_progress"}:
            return ReleaseAttempt.RUNNING
    return ReleaseAttempt.NONE


def _open_bumps(run_id: str) -> tuple[OpenBumpPr | None, tuple[OpenBumpPr, ...]]:
    proc = _gh(
        [
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            "1000",
            "--json",
            "number,headRefName,headRefOid,body,state",
        ]
    )
    payload = json.loads(proc.stdout.decode("utf-8").strip() or "[]")
    if not isinstance(payload, list):
        raise RuntimeError("gh pr list returned unexpected output")
    owned_raw = [pr for pr in payload if _body_has_run(pr.get("body"), run_id)]
    if len(owned_raw) > 1:
        raise RuntimeError("more than one pull request contains this bump-run-id")
    open_raw = [
        pr
        for pr in payload
        if _pr_state(pr) == "open" and str(pr.get("headRefName", "")).startswith(_BRANCH_PREFIX)
    ]
    selected: dict[int, dict] = {}
    for pr in open_raw:
        selected[int(pr["number"])] = pr
    if owned_raw:
        selected[int(owned_raw[0]["number"])] = owned_raw[0]
    built = {number: _to_pr(pr) for number, pr in selected.items()}
    owned = built[int(owned_raw[0]["number"])] if owned_raw else None
    return owned, tuple(built[int(pr["number"])] for pr in open_raw)


def _to_pr(pr: dict) -> OpenBumpPr:
    number = int(pr["number"])
    sha = str(pr["headRefOid"])
    _ensure_commit(sha, number)
    text = _blob_text(sha, MANIFEST_PATH)
    if text is None:
        raise RuntimeError(f"pull request {number} has no manifest")
    try:
        version = manifest_version(text)
    except ValueError as exc:
        raise RuntimeError(f"pull request {number} manifest does not parse") from exc
    return OpenBumpPr(
        number=number,
        branch=str(pr["headRefName"]),
        head_sha=sha,
        version=version,
        state=_pr_state(pr),
    )


def _ensure_commit(sha: str, pr_number: int) -> None:
    if _commit_exists(sha):
        return
    _run(["git", "fetch", "origin", f"refs/pull/{pr_number}/head"])
    if not _commit_exists(sha):
        raise RuntimeError(f"cannot read head {sha} for pull request {pr_number}")


def _commit_exists(sha: str) -> bool:
    proc = _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], check=False)
    return proc.returncode == 0


def _require_branch_diff(master_text: str, target: Version) -> None:
    expected = set_manifest_version(master_text, target)
    names = [
        line
        for line in _text(["git", "diff", "--name-only", "origin/master", "HEAD"]).splitlines()
        if line
    ]
    if not names:
        return
    head_text = _blob_text("HEAD", MANIFEST_PATH)
    if names == [MANIFEST_PATH] and head_text == expected:
        return
    raise RuntimeError("bump branch diff is not the manifest version change")


def _commit(message: str) -> None:
    actor = _env("GITHUB_ACTOR")
    email = f"{_env('GITHUB_ACTOR_ID')}+{actor}@users.noreply.github.com"
    _run(
        ["git", "commit", "-m", message, "--", MANIFEST_PATH],
        env={
            "GIT_AUTHOR_NAME": actor,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": actor,
            "GIT_COMMITTER_EMAIL": email,
        },
    )


def _open_pr(branch: str, title: str, run_id: str) -> None:
    owner = _env("GITHUB_REPOSITORY").split("/", 1)[0]
    listed = _gh(
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
            "number",
        ]
    )
    existing = json.loads(listed.stdout.decode("utf-8").strip() or "[]")
    if existing:
        return
    proc = _gh(
        [
            "pr",
            "create",
            "--base",
            "master",
            "--head",
            branch,
            "--title",
            title,
            "--body",
            f"bump-run-id: {run_id}\n",
        ],
        check=False,
    )
    if proc.returncode != 0 and "already exists" not in _combined(proc).lower():
        raise RuntimeError(_combined(proc).strip() or "pull request create failed")


def _ensure_run_id(number: int, run_id: str) -> None:
    proc = _gh(["pr", "view", str(number), "--json", "body"])
    body = json.loads(proc.stdout.decode("utf-8") or "{}").get("body") or ""
    needle = f"bump-run-id: {run_id}"
    if any(line.strip() == needle for line in body.splitlines()):
        return
    if body and not body.endswith("\n"):
        body += "\n"
    _gh(["pr", "edit", str(number), "--body", f"{body}{needle}\n"])


def _remote_tag_sha(name: str) -> str | None:
    text = _text(["git", "ls-remote", "origin", f"refs/tags/{name}", f"refs/tags/{name}^{{}}"])
    plain = None
    peeled = None
    for line in text.splitlines():
        if not line:
            continue
        sha, ref = line.split()
        if ref == f"refs/tags/{name}^{{}}":
            peeled = sha
        elif ref == f"refs/tags/{name}":
            plain = sha
    return peeled or plain


def _local_tag_sha(name: str) -> str | None:
    proc = _run(["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{name}^{{}}"], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8").strip() or None


def _remote_branch_sha(name: str) -> str | None:
    text = _text(["git", "ls-remote", "--heads", "origin", name]).strip()
    if not text:
        return None
    return text.split()[0]


def _blob_text(rev: str, path: str) -> str | None:
    proc = _run(["git", "cat-file", "blob", f"{rev}:{path}"], check=False)
    if proc.returncode == 0:
        return proc.stdout.decode("utf-8")
    detail = proc.stderr.decode("utf-8", errors="replace")
    if "Not a valid object name" in detail or "invalid object name" in detail:
        raise RuntimeError(f"cannot read {rev}")
    return None


def _body_has_run(body: object, run_id: str) -> bool:
    if not isinstance(body, str):
        return False
    needle = f"bump-run-id: {run_id}"
    return any(line.strip() == needle for line in body.splitlines())


def _pr_state(pr: dict) -> str:
    state = str(pr.get("state", "")).lower()
    if state not in {"open", "merged", "closed"}:
        raise RuntimeError(f"unexpected pull request state: {pr.get('state')}")
    return state


def gh_argv(args: list[str]) -> list[str]:
    return ["gh", *args]


def gh_repo_env() -> dict[str, str]:
    return {"GH_REPO": _env("GITHUB_REPOSITORY")}


def _gh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return _run(gh_argv(args), check=check, env=gh_repo_env())


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing")
    return value


def _text(args: list[str]) -> str:
    return _run(args).stdout.decode("utf-8")


def _run(
    args: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged.setdefault("GIT_TERMINAL_PROMPT", "0")
    merged.setdefault("GH_PROMPT_DISABLED", "1")
    proc = subprocess.run(args, capture_output=True, env=merged)
    if check and proc.returncode != 0:
        detail = _combined(proc).strip()
        raise RuntimeError(detail or f"command failed: {args[0]}")
    return proc


def _combined(proc: subprocess.CompletedProcess[bytes]) -> str:
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    return f"{stderr}\n{stdout}"


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


if __name__ == "__main__":
    sys.exit(main())
