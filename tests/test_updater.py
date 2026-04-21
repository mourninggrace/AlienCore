"""
End-to-end tests for core/updater.py and gui/whats_new_dialog.py.

The repo is currently private, so we can't hit api.github.com directly.
Instead we monkeypatch urllib.request.urlopen to return synthetic release
JSON + a locally built zipball, and run the updater against a temporary
scratch copy of AlienCore. Everything past the network boundary runs for
real — extract, bat-script generation, state persistence, changelog parse.
"""

import io
import json
import os
import re
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _FakeResp:
    """Minimal context-manager stand-in for urllib.request.urlopen's return."""
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body  = body
        self.headers = headers or {}
    def read(self, size: int = -1) -> bytes:
        if size < 0 or size >= len(self._body):
            out, self._body = self._body, b""
            return out
        out, self._body = self._body[:size], self._body[size:]
        return out
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _make_release_json(tag: str, zipball_url: str, body: str = "") -> bytes:
    return json.dumps({
        "tag_name":    f"v{tag}",
        "body":        body,
        "zipball_url": zipball_url,
        "html_url":    f"https://github.com/example/repo/releases/tag/v{tag}",
    }).encode("utf-8")


def _make_fake_zipball(file_contents: dict[str, str]) -> bytes:
    """
    Build a GitHub-style zipball in memory. GitHub wraps everything in a
    single top-level subdirectory named e.g. 'owner-repo-sha/' — the updater
    strips that on extract, so we replicate that layout here.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for rel, content in file_contents.items():
            z.writestr(f"owner-repo-abcdef/{rel}", content)
    return buf.getvalue()


@contextmanager
def _patched_urlopen(responses: dict[str, bytes]):
    """Patch urllib.request.urlopen to dispatch by URL prefix match."""
    def _fake(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for prefix, body in responses.items():
            if url.startswith(prefix):
                return _FakeResp(
                    body,
                    headers={"Content-Length": str(len(body))},
                )
        raise AssertionError(f"Unexpected URL fetched: {url}")
    with patch("urllib.request.urlopen", _fake):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# _version_tuple
# ─────────────────────────────────────────────────────────────────────────────

def test_version_tuple_orders_correctly():
    from core.updater import _version_tuple as vt
    assert vt("1.0.0")  < vt("1.0.1")
    assert vt("1.0.0")  < vt("1.1.0")
    assert vt("1.0.0")  < vt("2.0.0")
    assert vt("0.9.99") < vt("1.0.0")
    assert vt("1.0.0") == vt("1.0.0")


def test_version_tuple_survives_bad_input():
    from core.updater import _version_tuple as vt
    assert vt("")         == (0,)
    assert vt("garbage")  == (0,)
    # None would crash int(), so the except clause should catch it
    assert vt(None)       == (0,)


# ─────────────────────────────────────────────────────────────────────────────
# _do_check — fetch + parse + cache
# ─────────────────────────────────────────────────────────────────────────────

def _reset_updater_state():
    import core.updater as u
    u._cached.clear()
    u._checked = False
    u._dialog_pending.clear()


def test_do_check_detects_newer_version(tmp_path, monkeypatch):
    import core.updater as u
    _reset_updater_state()
    monkeypatch.setattr(u, "VERSION", "1.0.0")
    monkeypatch.setattr(u, "STATE_PATH", str(tmp_path / "update_state.json"))

    release = _make_release_json(
        "1.0.1",
        zipball_url="https://codeload.example.com/repo/zip/refs/tags/v1.0.1",
        body="- Cool new thing\n- Another fix\n",
    )
    with _patched_urlopen({u.GITHUB_API_URL: release}):
        u._do_check()

    info = u.get_update_info()
    assert info is not None
    assert info["version"] == "1.0.1"
    assert "Cool new thing" in info["notes"]
    assert info["zipball_url"].endswith("v1.0.1")
    assert u.is_dialog_pending(), "dialog should be flagged for display"


def test_do_check_ignores_older_or_equal_version(tmp_path, monkeypatch):
    import core.updater as u
    _reset_updater_state()
    monkeypatch.setattr(u, "VERSION", "1.0.0")
    monkeypatch.setattr(u, "STATE_PATH", str(tmp_path / "update_state.json"))

    release = _make_release_json(
        "1.0.0", zipball_url="https://example.com/zip")
    with _patched_urlopen({u.GITHUB_API_URL: release}):
        u._do_check()

    assert u.get_update_info() is None
    assert not u.is_dialog_pending()


def test_do_check_respects_dismissed_version(tmp_path, monkeypatch):
    import core.updater as u
    _reset_updater_state()
    monkeypatch.setattr(u, "VERSION", "1.0.0")
    state_path = tmp_path / "update_state.json"
    monkeypatch.setattr(u, "STATE_PATH", str(state_path))

    # Pre-seed a dismissal for the version we're about to detect
    state_path.write_text(json.dumps({
        "remind_after":      None,
        "dismissed_version": "1.0.1",
    }))

    release = _make_release_json("1.0.1", zipball_url="https://example.com/z")
    with _patched_urlopen({u.GITHUB_API_URL: release}):
        u._do_check()

    # Info is cached (button should show), but dialog must not fire
    assert u.get_update_info() is not None
    assert not u.is_dialog_pending()
    assert u.should_show_button() is True
    assert u.should_show_dialog() is False


# ─────────────────────────────────────────────────────────────────────────────
# State file: remind-later + dismiss
# ─────────────────────────────────────────────────────────────────────────────

def test_set_remind_later_writes_future_timestamp(tmp_path, monkeypatch):
    import core.updater as u
    monkeypatch.setattr(u, "STATE_PATH", str(tmp_path / "update_state.json"))
    before = __import__("time").time()
    u.set_remind_later()
    state = json.loads((tmp_path / "update_state.json").read_text())
    assert state["remind_after"] > before + 86000   # ~24h in the future
    assert state["dismissed_version"] is None


def test_set_dismissed_records_version(tmp_path, monkeypatch):
    import core.updater as u
    monkeypatch.setattr(u, "STATE_PATH", str(tmp_path / "update_state.json"))
    u.set_dismissed("1.0.5")
    state = json.loads((tmp_path / "update_state.json").read_text())
    assert state["dismissed_version"] == "1.0.5"
    assert state["remind_after"] is None


# ─────────────────────────────────────────────────────────────────────────────
# download_and_apply — exercise the full extract + bat-write pipeline
# ─────────────────────────────────────────────────────────────────────────────

def test_download_and_apply_builds_update_bat(tmp_path, monkeypatch):
    """
    Runs download_and_apply against a temp app directory and a synthesised
    zipball. We monkeypatch subprocess.Popen and sys.exit so nothing
    actually launches the installer / kills the test runner.
    """
    import core.updater as u

    # Temp "installed" copy of AlienCore
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "aliencore.py").write_text("# old\n")
    (app_dir / "core").mkdir()
    (app_dir / "core" / "constants.py").write_text('VERSION = "1.0.0"\n')

    # Synthetic zipball content — simulates a v1.0.1 release
    zipball_bytes = _make_fake_zipball({
        "aliencore.py":         "# new\n",
        "core/constants.py":    'VERSION = "1.0.1"\n',
        "CHANGELOG.md":         "## [1.0.1] — 2026-05-01\n- test release\n",
    })

    monkeypatch.setattr(u, "BASE_DIR", str(app_dir))
    monkeypatch.setattr(u, "STATE_PATH", str(app_dir / "update_state.json"))

    # Capture Popen instead of launching the update bat
    popen_calls = []
    def _fake_popen(cmd, *a, **kw):
        popen_calls.append(cmd)
        class _P: pass
        return _P()
    monkeypatch.setattr("subprocess.Popen", _fake_popen)

    # Swallow the sys.exit at the end so pytest keeps running
    exit_calls = []
    monkeypatch.setattr("sys.exit", lambda code=0: exit_calls.append(code))

    # Collect progress callbacks
    progress = []
    def on_progress(pct, msg): progress.append((pct, msg))

    with _patched_urlopen({"https://example.com/zipball": zipball_bytes}):
        u.download_and_apply("https://example.com/zipball", on_progress=on_progress)

    # Progress fired at least for Downloading → Extracting → Launching
    msgs = " | ".join(m for _, m in progress)
    assert "Downloading" in msgs
    assert "Extracting"  in msgs
    assert "Launching"   in msgs

    # Popen got called exactly once with cmd.exe /c <bat>
    assert len(popen_calls) == 1
    cmd = popen_calls[0]
    assert cmd[0] == "cmd.exe" and cmd[1] == "/c"
    bat_path = cmd[2]
    assert bat_path.endswith("do_update.bat")

    # The bat script should reference the extracted content and the app_dir
    bat_body = open(bat_path, "r", encoding="utf-8").read()
    assert "robocopy" in bat_body
    assert str(app_dir) in bat_body
    assert "aliencore.py" in bat_body

    # sys.exit(0) was called at the end
    assert exit_calls == [0]


# ─────────────────────────────────────────────────────────────────────────────
# CHANGELOG.md parser (gui.whats_new_dialog)
# ─────────────────────────────────────────────────────────────────────────────

def test_changelog_parser_extracts_current_version_section():
    """The real CHANGELOG.md must have a parseable v1.0.0 section."""
    from gui.whats_new_dialog import _load_notes_for_version
    notes = _load_notes_for_version("1.0.0")
    assert notes, "CHANGELOG.md must contain a [1.0.0] section"
    assert "Initial public release" in notes or "Added" in notes


def test_changelog_parser_returns_empty_for_missing_version():
    from gui.whats_new_dialog import _load_notes_for_version
    assert _load_notes_for_version("99.99.99") == ""


def test_changelog_parser_handles_multi_version_file(tmp_path, monkeypatch):
    """With 2 version sections, the parser must stop at the next header."""
    import gui.whats_new_dialog as wn
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n"
        "\n"
        "## [1.0.1] — 2026-05-01\n"
        "- bugfix A\n"
        "- bugfix B\n"
        "\n"
        "## [1.0.0] — 2026-04-21\n"
        "- initial release\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(wn, "_CHANGELOG_PATH", str(changelog))
    latest = wn._load_notes_for_version("1.0.1")
    old    = wn._load_notes_for_version("1.0.0")
    assert "bugfix A" in latest and "initial release" not in latest
    assert "initial release" in old


# ─────────────────────────────────────────────────────────────────────────────
# What's New state tracking
# ─────────────────────────────────────────────────────────────────────────────

def test_whats_new_marks_seen_on_first_run(tmp_path, monkeypatch):
    """First-run users record the current version without seeing the dialog."""
    import gui.whats_new_dialog as wn
    state = tmp_path / "update_state.json"
    monkeypatch.setattr(wn, "_STATE_PATH", str(state))
    monkeypatch.setattr(wn, "VERSION", "1.0.0")

    # If show_if_updated tries to open a dialog, the test fails (no DISPLAY).
    monkeypatch.setattr(wn, "_show_dialog",
                        lambda *a, **kw: pytest.fail("dialog shown on first run"))
    wn.show_if_updated(is_first_run=True)
    s = json.loads(state.read_text())
    assert s["last_seen_version"] == "1.0.0"


def test_whats_new_fires_on_upgrade(tmp_path, monkeypatch):
    """Returning users on a bumped version see the dialog exactly once."""
    import gui.whats_new_dialog as wn
    state = tmp_path / "update_state.json"
    state.write_text(json.dumps({"last_seen_version": "1.0.0"}))
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [1.0.1] — 2026-05-01\n"
        "- new hotness\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(wn, "_STATE_PATH",     str(state))
    monkeypatch.setattr(wn, "_CHANGELOG_PATH", str(changelog))
    monkeypatch.setattr(wn, "VERSION",         "1.0.1")

    shown = []
    monkeypatch.setattr(wn, "_show_dialog",
                        lambda v, notes, previous: shown.append((v, notes, previous)))

    wn.show_if_updated(is_first_run=False)
    assert len(shown) == 1
    v, notes, prev = shown[0]
    assert v == "1.0.1"
    assert prev == "1.0.0"
    assert "new hotness" in notes

    # Second call (same version) must not re-trigger
    shown.clear()
    wn.show_if_updated(is_first_run=False)
    assert shown == []


def test_whats_new_skips_when_no_prior_version_and_not_first_run(tmp_path, monkeypatch):
    """
    Edge case: if the state file is missing or has no last_seen_version but
    is_first_run is False (e.g. first launch after adding this feature),
    treat it as a fresh seen rather than spamming old notes.
    """
    import gui.whats_new_dialog as wn
    state = tmp_path / "update_state.json"
    monkeypatch.setattr(wn, "_STATE_PATH", str(state))
    monkeypatch.setattr(wn, "VERSION", "1.0.0")
    monkeypatch.setattr(wn, "_show_dialog",
                        lambda *a, **kw: pytest.fail("should not show"))
    wn.show_if_updated(is_first_run=False)
    s = json.loads(state.read_text())
    assert s["last_seen_version"] == "1.0.0"
