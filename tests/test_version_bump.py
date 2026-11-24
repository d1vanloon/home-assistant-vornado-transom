from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.version_bump.__main__ import gh_argv, gh_repo_env
from tools.version_bump.bump import (
    MANIFEST_PATH,
    CreateTag,
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
    bump_branch,
    bump_version,
    commit_message,
    decide,
    parse_part,
    parse_version,
    set_manifest_version,
    tag_name,
)


def test_bump_version_from_0_3_0() -> None:
    version = parse_version("0.3.0")
    assert bump_version(version, Part.MINOR) == parse_version("0.4.0")
    assert bump_version(version, Part.PATCH) == parse_version("0.3.1")
    assert bump_version(version, Part.MAJOR) == parse_version("1.0.0")


def test_bump_version_from_1_2_3() -> None:
    version = parse_version("1.2.3")
    assert bump_version(version, Part.MAJOR) == parse_version("2.0.0")
    assert bump_version(version, Part.MINOR) == parse_version("1.3.0")
    assert bump_version(version, Part.PATCH) == parse_version("1.2.4")


def test_parse_part_blank_is_minor() -> None:
    assert parse_part(None) is Part.MINOR
    assert parse_part("") is Part.MINOR
    assert parse_part("   ") is Part.MINOR
    assert parse_part("minor") is Part.MINOR
    assert parse_part("patch") is Part.PATCH
    assert parse_part("major") is Part.MAJOR
    with pytest.raises(ValueError):
        parse_part("nope")


def test_parse_version_rejects_prefixes_and_leading_zeros() -> None:
    for text in ("v0.3.0", "0.3", "0.3.0-beta", "01.2.3", "1.2.3.4"):
        with pytest.raises(ValueError):
            parse_version(text)
    assert (parse_version("0.0.0").major, parse_version("0.0.0").minor, parse_version("0.0.0").patch) == (0, 0, 0)


def test_set_manifest_version_changes_only_the_real_manifest_token() -> None:
    path = Path(__file__).resolve().parents[1] / MANIFEST_PATH
    raw = path.read_bytes()
    original = raw.decode("utf-8")
    updated = set_manifest_version(original, parse_version("0.4.0"))
    longer = set_manifest_version(original, parse_version("10.20.30"))
    assert updated == original.replace('"version": "0.3.0"', '"version": "0.4.0"', 1)
    assert longer == original.replace('"version": "0.3.0"', '"version": "10.20.30"', 1)
    old = json.loads(original)
    new = json.loads(updated)
    assert new["version"] == "0.4.0"
    del old["version"]
    del new["version"]
    assert old == new
    assert path.read_bytes() == raw


def test_set_manifest_version_keeps_nested_and_rejects_bad_tokens() -> None:
    text = '{\n  "documentation": "version 0.3.0",\n  "version": "0.3.0",\n  "extra": {"version": "9.9.9"}\n}\n'
    updated = set_manifest_version(text, parse_version("1.2.4"))
    assert updated == text.replace('"version": "0.3.0"', '"version": "1.2.4"', 1)
    with pytest.raises(ValueError):
        set_manifest_version('{"name": "x"}\n', parse_version("1.0.0"))
    repeated = '{"version": "1.0.0", "version": "1.0.0"}\n'
    with pytest.raises(ValueError):
        set_manifest_version(repeated, parse_version("1.0.1"))


def test_published_minor_opens_bump() -> None:
    decision = decide(_published(), Part.MINOR)
    assert isinstance(decision, OpenBump)
    assert decision.source == parse_version("0.3.0")
    assert decision.target == parse_version("0.4.0")
    assert decision.base_sha == "master"
    assert decision.branch == "version-bump/v0.4.0"
    assert decision.commit_message == "Bump version to 0.4.0 in manifest.json"


def test_untagged_head_creates_tag_for_any_part() -> None:
    snapshot = _published(include_tag=False, release=ReleaseAttempt.NONE)
    for part in (Part.MAJOR, Part.MINOR, Part.PATCH):
        decision = decide(snapshot, part)
        assert isinstance(decision, CreateTag)
        assert decision.version == parse_version("0.3.0")
        assert decision.sha == "introduce"


def test_missing_release_dispatches() -> None:
    decision = decide(_published(release=ReleaseAttempt.NONE), Part.PATCH)
    assert isinstance(decision, DispatchRelease)
    assert decision.version == parse_version("0.3.0")
    assert decision.sha == "introduce"


def test_running_release_is_done() -> None:
    decision = decide(_published(release=ReleaseAttempt.RUNNING), Part.MAJOR)
    assert isinstance(decision, Done)
    assert decision.message == "Release for v0.3.0 is still running"


def test_tag_on_the_wrong_commit_is_refused() -> None:
    decision = decide(_published(tag_sha="other"), Part.MINOR)
    assert isinstance(decision, Refuse)
    assert decision.message == "tag points at the wrong commit"


def test_owned_published_version_is_done_for_any_part() -> None:
    owned = OpenBumpPr(
        number=4,
        branch="version-bump/v0.3.0",
        head_sha="head",
        version=parse_version("0.3.0"),
        state="merged",
    )
    snapshot = _published(owned=owned)
    for part in (Part.MAJOR, Part.MINOR, Part.PATCH):
        decision = decide(snapshot, part)
        assert isinstance(decision, Done)
        assert decision.message == "v0.3.0 is already published"


def test_new_run_with_published_master_opens_bump() -> None:
    decision = decide(_published(owned=None), Part.PATCH)
    assert isinstance(decision, OpenBump)
    assert decision.target == parse_version("0.3.1")
    assert decision.branch == bump_branch(decision.target)
    assert decision.commit_message == commit_message(decision.target)


def test_open_pull_request_for_the_target_merges() -> None:
    target = parse_version("0.4.0")
    pr = OpenBumpPr(
        number=9,
        branch=bump_branch(target),
        head_sha="headsha",
        version=target,
        state="open",
    )
    decision = decide(_published(open_bumps=(pr,)), Part.MINOR)
    assert isinstance(decision, MergeBump)
    assert decision.pr_number == 9
    assert decision.expected_head_sha == "headsha"
    assert decision.target == target
    assert decision.commit_message == commit_message(target)


def test_gh_api_command_does_not_pass_repo_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "d1vanloon/home-assistant-vornado-transom")
    argv = gh_argv(
        [
            "api",
            "--method",
            "PUT",
            "repos/d1vanloon/home-assistant-vornado-transom/pulls/1/merge",
        ]
    )
    assert argv[0] == "gh"
    assert "--repo" not in argv
    assert gh_repo_env() == {"GH_REPO": "d1vanloon/home-assistant-vornado-transom"}


def test_introduce_sha_missing_is_refused() -> None:
    decision = decide(_published(introduce_sha=None), Part.MINOR)
    assert isinstance(decision, Refuse)
    assert decision.message == "cannot find the commit that introduced the manifest version"


def test_tag_name_is_the_only_formatter() -> None:
    version = parse_version("0.3.0")
    assert tag_name(version) == "v0.3.0"
    assert bump_branch(version) == "version-bump/v0.3.0"


def _published(
    version: str = "0.3.0",
    *,
    release: ReleaseAttempt = ReleaseAttempt.PUBLISHED,
    introduce_sha: str | None = "introduce",
    tag_sha: str = "introduce",
    include_tag: bool = True,
    open_bumps: tuple[OpenBumpPr, ...] = (),
    owned: OpenBumpPr | None = None,
) -> Snapshot:
    current = parse_version(version)
    tags: dict[Version, TagState] = {}
    if include_tag:
        tags[current] = TagState(sha=tag_sha, manifest_version=current)
    return Snapshot(
        master_sha="master",
        master_version=current,
        introduce_sha=introduce_sha,
        tags=tags,
        master_release=release,
        open_bumps=open_bumps,
        owned=owned,
    )
