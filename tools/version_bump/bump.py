from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

MANIFEST_PATH = "custom_components/vornado_transom/manifest.json"


class Part(Enum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


class ReleaseAttempt(Enum):
    NONE = "none"
    RUNNING = "running"
    PUBLISHED = "published"


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int


@dataclass(frozen=True)
class TagState:
    sha: str
    manifest_version: Version


@dataclass(frozen=True)
class OpenBumpPr:
    number: int
    branch: str
    head_sha: str
    version: Version
    state: str


@dataclass(frozen=True)
class Snapshot:
    master_sha: str
    master_version: Version
    introduce_sha: str | None
    tags: Mapping[Version, TagState]
    master_release: ReleaseAttempt
    open_bumps: tuple[OpenBumpPr, ...]
    owned: OpenBumpPr | None


@dataclass(frozen=True)
class CreateTag:
    version: Version
    sha: str


@dataclass(frozen=True)
class DispatchRelease:
    version: Version
    sha: str


@dataclass(frozen=True)
class OpenBump:
    source: Version
    target: Version
    base_sha: str
    branch: str
    commit_message: str


@dataclass(frozen=True)
class MergeBump:
    pr_number: int
    expected_head_sha: str
    target: Version
    commit_message: str


@dataclass(frozen=True)
class Done:
    message: str


@dataclass(frozen=True)
class Refuse:
    message: str


Decision = CreateTag | DispatchRelease | OpenBump | MergeBump | Done | Refuse


def bump_version(version: Version, part: Part) -> Version:
    if part is Part.MAJOR:
        return Version(version.major + 1, 0, 0)
    if part is Part.MINOR:
        return Version(version.major, version.minor + 1, 0)
    if part is Part.PATCH:
        return Version(version.major, version.minor, version.patch + 1)
    raise ValueError(f"unknown part: {part}")


def parse_part(text: str | None) -> Part:
    if text is None or text.strip() == "":
        return Part.MINOR
    if text == "minor":
        return Part.MINOR
    if text == "patch":
        return Part.PATCH
    if text == "major":
        return Part.MAJOR
    raise ValueError(f"unknown part: {text}")


def parse_version(text: str) -> Version:
    pieces = text.split(".")
    if len(pieces) != 3 or any(not _is_component(piece) for piece in pieces):
        raise ValueError(f"invalid version: {text}")
    return Version(int(pieces[0]), int(pieces[1]), int(pieces[2]))


def tag_name(version: Version) -> str:
    return f"v{version.major}.{version.minor}.{version.patch}"


def bump_branch(version: Version) -> str:
    return f"version-bump/{tag_name(version)}"


def commit_message(version: Version) -> str:
    return f"Bump version to {version.major}.{version.minor}.{version.patch} in manifest.json"


def manifest_version(text: str) -> Version:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("manifest does not parse") from exc
    if not isinstance(document, dict):
        raise ValueError("manifest does not parse")
    value = document.get("version")
    if not isinstance(value, str):
        raise ValueError("manifest does not parse")
    return parse_version(value)


def set_manifest_version(text: str, new: Version) -> str:
    start = _skip_ws(text, 0)
    if start >= len(text) or text[start] != "{":
        raise ValueError("manifest version token is missing or repeated")
    spans: list[tuple[int, int]] = []
    _scan_object(text, start, spans)
    if len(spans) != 1:
        raise ValueError("manifest version token is missing or repeated")
    token_start, token_end = spans[0]
    updated = f"{text[:token_start]}{new.major}.{new.minor}.{new.patch}{text[token_end:]}"
    _assert_only_version_changed(text, updated, new)
    return updated


def decide(snapshot: Snapshot, part: Part) -> Decision:
    version = snapshot.master_version
    if snapshot.introduce_sha is None:
        return Refuse("cannot find the commit that introduced the manifest version")

    tag = snapshot.tags.get(version)
    if tag is None:
        return CreateTag(version, snapshot.introduce_sha)

    if tag.manifest_version != version or tag.sha != snapshot.introduce_sha:
        return Refuse("tag points at the wrong commit")

    if snapshot.master_release is ReleaseAttempt.RUNNING:
        return Done(f"Release for {tag_name(version)} is still running")

    if snapshot.master_release is not ReleaseAttempt.PUBLISHED:
        return DispatchRelease(version, tag.sha)

    if snapshot.owned is not None and snapshot.owned.version == version:
        return Done(f"{tag_name(version)} is already published")

    if (
        snapshot.owned is not None
        and snapshot.owned.state == "merged"
        and snapshot.owned.version != version
    ):
        return Refuse("this run already merged a different version")

    target = snapshot.owned.version if snapshot.owned is not None else bump_version(version, part)

    if target in snapshot.tags:
        return Refuse("the target tag already exists")

    wanted_branch = bump_branch(target)
    matches = [
        pr
        for pr in snapshot.open_bumps
        if pr.version == target and pr.branch == wanted_branch and pr.state == "open"
    ]
    others = [
        pr
        for pr in snapshot.open_bumps
        if pr.state == "open" and pr not in matches
    ]
    if others or len(matches) > 1:
        return Refuse("an open bump pull request does not match the target")
    if len(matches) == 1:
        pr = matches[0]
        return MergeBump(pr.number, pr.head_sha, target, commit_message(target))
    return OpenBump(version, target, snapshot.master_sha, wanted_branch, commit_message(target))


def _is_component(text: str) -> bool:
    if text == "0":
        return True
    return len(text) > 0 and text[0] != "0" and text.isdigit()


def _assert_only_version_changed(before: str, after: str, new: Version) -> None:
    try:
        old_document = json.loads(before)
        new_document = json.loads(after)
    except json.JSONDecodeError as exc:
        raise ValueError("manifest does not parse") from exc
    if not isinstance(old_document, dict) or not isinstance(new_document, dict):
        raise ValueError("manifest changed a field other than version")
    if set(old_document) != set(new_document):
        raise ValueError("manifest changed a field other than version")
    for key, value in old_document.items():
        if key == "version":
            continue
        if new_document[key] != value:
            raise ValueError("manifest changed a field other than version")
    if new_document.get("version") != f"{new.major}.{new.minor}.{new.patch}":
        raise ValueError("manifest version was not updated")


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _parse_string(text: str, index: int) -> tuple[str, int, int, int]:
    if index >= len(text) or text[index] != '"':
        raise ValueError("manifest does not parse")
    index += 1
    raw_start = index
    chars: list[str] = []
    while index < len(text):
        char = text[index]
        if char == '"':
            return "".join(chars), raw_start, index, index + 1
        if char == "\\":
            chars.append(_unescape(text, index))
            index = _escape_end(text, index)
            continue
        if ord(char) < 0x20:
            raise ValueError("manifest does not parse")
        chars.append(char)
        index += 1
    raise ValueError("manifest does not parse")


def _unescape(text: str, index: int) -> str:
    if index + 1 >= len(text):
        raise ValueError("manifest does not parse")
    escaped = text[index + 1]
    mapping = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    if escaped in mapping:
        return mapping[escaped]
    if escaped != "u" or index + 6 > len(text):
        raise ValueError("manifest does not parse")
    hex_digits = text[index + 2 : index + 6]
    if any(digit not in "0123456789abcdefABCDEF" for digit in hex_digits):
        raise ValueError("manifest does not parse")
    return chr(int(hex_digits, 16))


def _escape_end(text: str, index: int) -> int:
    if index + 1 >= len(text):
        raise ValueError("manifest does not parse")
    if text[index + 1] == "u":
        return index + 6
    return index + 2


def _expect(text: str, index: int, literal: str) -> int:
    end = index + len(literal)
    if text[index:end] != literal:
        raise ValueError("manifest does not parse")
    return end


def _skip_number(text: str, index: int) -> int:
    if text[index] == "-":
        index += 1
    digits = index
    while index < len(text) and text[index].isdigit():
        index += 1
    if index == digits:
        raise ValueError("manifest does not parse")
    if index < len(text) and text[index] == ".":
        index += 1
        fraction = index
        while index < len(text) and text[index].isdigit():
            index += 1
        if index == fraction:
            raise ValueError("manifest does not parse")
    if index < len(text) and text[index] in "eE":
        index += 1
        if index < len(text) and text[index] in "+-":
            index += 1
        exponent = index
        while index < len(text) and text[index].isdigit():
            index += 1
        if index == exponent:
            raise ValueError("manifest does not parse")
    return index


def _skip_value(text: str, index: int) -> int:
    index = _skip_ws(text, index)
    if index >= len(text):
        raise ValueError("manifest does not parse")
    char = text[index]
    if char == '"':
        _, _, _, end = _parse_string(text, index)
        return end
    if char == "{":
        return _scan_object(text, index, None)
    if char == "[":
        return _scan_array(text, index)
    if char == "t":
        return _expect(text, index, "true")
    if char == "f":
        return _expect(text, index, "false")
    if char == "n":
        return _expect(text, index, "null")
    if char == "-" or char.isdigit():
        return _skip_number(text, index)
    raise ValueError("manifest does not parse")


def _scan_array(text: str, index: int) -> int:
    index += 1
    index = _skip_ws(text, index)
    if index < len(text) and text[index] == "]":
        return index + 1
    while index < len(text):
        index = _skip_value(text, index)
        index = _skip_ws(text, index)
        if index >= len(text):
            break
        if text[index] == ",":
            index += 1
            continue
        if text[index] == "]":
            return index + 1
        break
    raise ValueError("manifest does not parse")


def _scan_object(text: str, index: int, spans: list[tuple[int, int]] | None) -> int:
    index += 1
    index = _skip_ws(text, index)
    if index < len(text) and text[index] == "}":
        return index + 1
    while index < len(text):
        index = _skip_ws(text, index)
        key, _, _, index = _parse_string(text, index)
        index = _skip_ws(text, index)
        if index >= len(text) or text[index] != ":":
            raise ValueError("manifest does not parse")
        index = _skip_ws(text, index + 1)
        if spans is not None and key == "version":
            if index >= len(text) or text[index] != '"':
                raise ValueError("manifest version token is missing or repeated")
            _, raw_start, raw_end, index = _parse_string(text, index)
            spans.append((raw_start, raw_end))
        else:
            index = _skip_value(text, index)
        index = _skip_ws(text, index)
        if index >= len(text):
            break
        if text[index] == ",":
            index += 1
            continue
        if text[index] == "}":
            return index + 1
        break
    raise ValueError("manifest does not parse")
