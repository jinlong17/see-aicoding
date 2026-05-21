"""Cursor / VS Code extension inventory scanner.

Detects which AI coding extensions are installed in ~/.cursor/extensions/
and ~/.vscode/extensions/. Used by the Cursor zone to show "what's installed"
even when an extension isn't currently running a subprocess.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Known AI extension IDs and how to label them.
# Each entry: ext_id_prefix → (display_name, family, color)
# Family used for visual grouping: claude / openai / copilot / cline / continue / cody / other
KNOWN_AI_EXTENSIONS: dict[str, tuple[str, str, str]] = {
    "anthropic.claude-code": ("Claude Code", "claude", "#B48CFF"),
    "openai.chatgpt": ("OpenAI ChatGPT", "openai", "#30D5A8"),
    "openai.codex": ("OpenAI Codex", "openai", "#30D5A8"),
    "github.copilot": ("GitHub Copilot", "copilot", "#8FB4FF"),
    "github.copilot-chat": ("Copilot Chat", "copilot", "#8FB4FF"),
    "saoudrizwan.claude-dev": ("Cline (Claude Dev)", "cline", "#D6B6FF"),
    "continue.continue": ("Continue", "continue", "#F0C987"),
    "sourcegraph.cody-ai": ("Cody", "cody", "#F2A0A8"),
    "tabnine.tabnine-vscode": ("Tabnine", "tabnine", "#A1ACB8"),
    "codeium.codeium": ("Codeium", "codeium", "#9AD8C9"),
}


@dataclass
class ExtensionInfo:
    ext_id: str            # full directory name e.g. "anthropic.claude-code-2.1.118-darwin-arm64"
    short_id: str          # short id without version, e.g. "anthropic.claude-code"
    display_name: str
    family: str
    color: str
    version: str
    host: str              # "cursor" or "vscode"
    path: Path             # full directory path


_PLATFORM_SUFFIXES = ("-darwin-arm64", "-darwin-x64", "-linux-x64", "-linux-arm64",
                      "-win32-x64", "-win32-arm64", "-universal", "-alpine-x64")


def _parse_ext_dir(dirname: str) -> tuple[str | None, str | None]:
    """Return (short_id, version) parsed from an extension directory name.

    Extension dirs look like:
        "anthropic.claude-code-2.1.118-darwin-arm64"
        "openai.chatgpt-26.409.20454"
        "github.copilot-chat-0.31.0"

    The "id" can itself contain hyphens (claude-code, copilot-chat) so we
    can't split greedily — we match each known prefix.
    """
    for short_id in KNOWN_AI_EXTENSIONS:
        if not dirname.startswith(short_id + "-"):
            continue
        rest = dirname[len(short_id) + 1:]
        # Strip platform suffix if present.
        for suffix in _PLATFORM_SUFFIXES:
            if rest.endswith(suffix):
                rest = rest[: -len(suffix)]
                break
        # What remains should look like a version.
        if rest and rest[0].isdigit():
            return short_id, rest
    return None, None


def _version_tuple(v: str) -> tuple:
    """Parse a version string into a comparable tuple. Falls back to string."""
    parts: list = []
    for piece in re.split(r"[.\-+]", v):
        if piece.isdigit():
            parts.append((0, int(piece)))
        else:
            parts.append((1, piece))
    return tuple(parts)


def _scan_dir(root: Path, host: str) -> list[ExtensionInfo]:
    if not root.is_dir():
        return []
    out: list[ExtensionInfo] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        short_id, version = _parse_ext_dir(entry.name)
        if not short_id or short_id not in KNOWN_AI_EXTENSIONS:
            continue
        display, family, color = KNOWN_AI_EXTENSIONS[short_id]
        out.append(ExtensionInfo(
            ext_id=entry.name,
            short_id=short_id,
            display_name=display,
            family=family,
            color=color,
            version=version or "",
            host=host,
            path=entry,
        ))
    return out


def scan_installed_extensions(latest_only: bool = True) -> list[ExtensionInfo]:
    """Scan Cursor + VS Code extension dirs for known AI tools.

    latest_only: collapse to the newest version per (short_id, host) pair —
    extension hosts keep stale older versions on disk after updates.
    """
    home = Path.home()
    found: list[ExtensionInfo] = []
    found.extend(_scan_dir(home / ".cursor" / "extensions", "cursor"))
    found.extend(_scan_dir(home / ".vscode" / "extensions", "vscode"))

    if latest_only:
        # Keep only the highest-versioned entry per (short_id, host).
        best: dict[tuple[str, str], ExtensionInfo] = {}
        for ext in found:
            key = (ext.short_id, ext.host)
            cur = best.get(key)
            if cur is None or _version_tuple(ext.version) > _version_tuple(cur.version):
                best[key] = ext
        found = list(best.values())

    found.sort(key=lambda e: (e.family, e.short_id, e.host))
    return found


def group_by_family(extensions: list[ExtensionInfo]) -> dict[str, list[ExtensionInfo]]:
    """Group extensions by family, newest version first within each."""
    groups: dict[str, list[ExtensionInfo]] = {}
    for ext in extensions:
        groups.setdefault(ext.family, []).append(ext)
    for fam, lst in groups.items():
        lst.sort(key=lambda e: _version_tuple(e.version), reverse=True)
    return groups
