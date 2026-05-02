"""
AlienCore - tools/test_updater_e2e.py
End-to-end verification of the updater against a real localhost HTTP server.

Unlike `tests/test_updater.py` (which monkeypatches `urllib.request.urlopen`),
this exercises the full _do_check -> download_and_apply pipeline with real
network I/O — catches bugs that would only surface in production:
  · Content-Length / chunked-transfer handling
  · Real socket timeouts and partial reads
  · zipfile parsing of an actual zip on disk (not from BytesIO)
  · The new _safe_extract_member zip-slip path
  · The new expected_version cross-check against the bundled core/constants.py
  · sha256 line parsing from a realistic GitHub release body

Self-contained: synthesizes a real zipball, serves it from
http.server on 127.0.0.1:8888, runs the real updater paths, mocks
only subprocess.Popen and sys.exit so the actual update bat does
NOT run (it would clobber this dev tree).

Run:  python tools/test_updater_e2e.py
"""

import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

# Run from the repo root so the import below resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import updater  # noqa: E402


PORT      = 8888
TEST_TAG  = "1.0.99"          # purely numeric — _version_tuple parses it
TEST_ROOT = "mourninggrace-AlienCore-deadbeef1234"


def _build_zipball() -> bytes:
    """Synthesize a release zipball with the same wrapper-dir layout that
    GitHub produces for tag tarballs."""
    buf = io.BytesIO()
    files = {
        f"{TEST_ROOT}/aliencore.py":      "# e2e test build entrypoint\n",
        f"{TEST_ROOT}/core/__init__.py":  "",
        f"{TEST_ROOT}/core/constants.py": (
            f'APP_NAME = "AlienCore"\n'
            f'VERSION  = "{TEST_TAG}"\n'
            'BASE_DIR = ""\n'
        ),
        f"{TEST_ROOT}/CHANGELOG.md": (
            f"## [{TEST_TAG}]\n- end-to-end test release, do not ship\n"
        ),
    }
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def main():
    zip_bytes  = _build_zipball()
    real_sha   = hashlib.sha256(zip_bytes).hexdigest()
    print(f"Synthesized zipball: {len(zip_bytes)} bytes, sha256={real_sha[:16]}...")

    release_json = {
        "tag_name":    f"v{TEST_TAG}",
        "name":        f"AlienCore v{TEST_TAG} (E2E test)",
        "body":        (
            "This is an end-to-end test release.\n"
            "Do not install this on a real machine.\n\n"
            f"sha256: {real_sha}\n"
        ),
        "zipball_url": f"http://127.0.0.1:{PORT}/zipball",
        "html_url":    f"http://127.0.0.1:{PORT}/release-page",
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):  # quiet
            pass

        def do_GET(self):
            if self.path == "/releases/latest":
                body = json.dumps(release_json).encode()
                self.send_response(200)
                self.send_header("Content-Type",   "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/zipball":
                self.send_response(200)
                self.send_header("Content-Type",   "application/zip")
                self.send_header("Content-Length", str(len(zip_bytes)))
                self.end_headers()
                self.wfile.write(zip_bytes)
            else:
                self.send_error(404)

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"Mock server listening on http://127.0.0.1:{PORT}/")

    # Isolated temp install dir so the real robocopy in do_update.bat
    # would target this — not C:\aliencore.  Pre-populate with an "old"
    # install for realism.
    tmp_app = tempfile.mkdtemp(prefix="aliencore_e2e_app_")
    os.makedirs(os.path.join(tmp_app, "core"))
    with open(os.path.join(tmp_app, "aliencore.py"), "w") as f:
        f.write("# OLD\n")
    with open(os.path.join(tmp_app, "core", "constants.py"), "w") as f:
        f.write('VERSION = "1.0.0"\n')
    print(f"Temp install dir: {tmp_app}")

    # Patch module globals
    saved = {
        "GITHUB_API_URL": updater.GITHUB_API_URL,
        "BASE_DIR":       updater.BASE_DIR,
        "STATE_PATH":     updater.STATE_PATH,
        "VERSION":        updater.VERSION,
        "_cached":        dict(updater._cached),
    }
    updater.GITHUB_API_URL = f"http://127.0.0.1:{PORT}/releases/latest"
    updater.BASE_DIR       = tmp_app
    updater.STATE_PATH     = os.path.join(tmp_app, "update_state.json")
    updater.VERSION        = "1.0.0"      # so the synthesized 1.0.99 IS newer
    updater._cached.clear()

    # Mock Popen and sys.exit so the bat does NOT run and the test process
    # doesn't suicide.  Everything else (urllib, zipfile, hashlib, file I/O)
    # runs for real.
    import subprocess as _sp
    orig_popen, orig_exit = _sp.Popen, sys.exit
    popen_calls, exit_calls = [], []
    _sp.Popen = lambda cmd, *a, **kw: (popen_calls.append(cmd) or type("P", (), {})())
    sys.exit = lambda code=0: exit_calls.append(code)

    try:
        # ── Phase 1: _do_check picks up the new release ──────────────────────
        print("\n[1/4] _do_check() — discover new release")
        updater._do_check()
        info = updater.get_update_info()
        assert info, "No update info returned"
        assert info["version"]     == TEST_TAG, f"version: {info['version']!r}"
        assert info["sha256"]      == real_sha, "sha256 line parsing failed"
        assert info["zipball_url"] == release_json["zipball_url"]
        print(f"  OK version    = {info['version']}")
        print(f"  OK sha256     = {info['sha256'][:16]}... (matches)")
        print(f"  OK zipball    = {info['zipball_url']}")

        # ── Phase 2: download_and_apply runs end-to-end ─────────────────────
        print("\n[2/4] download_and_apply() — full pipeline")
        progress = []
        updater.download_and_apply(
            info["zipball_url"],
            on_progress=lambda pct, msg: progress.append((pct, msg)),
            expected_sha256=info["sha256"],
            expected_version=info["version"],
        )
        msgs = " | ".join(m for _, m in progress)
        assert "Downloading" in msgs
        assert "Verifying"   in msgs
        assert "Extracting"  in msgs
        assert "Launching"   in msgs
        print(f"  OK progress phases: Downloading -> Verifying -> Extracting -> Launching")

        assert len(popen_calls) == 1, f"Expected 1 Popen call, got {len(popen_calls)}"
        bat_cmd = popen_calls[0]
        assert bat_cmd[0] == "cmd.exe" and bat_cmd[1] == "/c"
        bat_path = bat_cmd[2]
        with open(bat_path) as f:
            bat = f.read()
        assert "robocopy" in bat
        assert tmp_app in bat, "Bat must reference the (mocked) install dir"
        print(f"  OK subprocess.Popen called with cmd.exe /c <bat>")
        print(f"  OK bat targets the temp install dir")

        m = re.search(r'robocopy "([^"]+)"', bat)
        assert m, "Couldn't parse robocopy source from bat"
        extracted_root = m.group(1)
        ec = os.path.join(extracted_root, "core", "constants.py")
        assert os.path.exists(ec), "Extracted core/constants.py missing"
        with open(ec) as f:
            assert TEST_TAG in f.read()
        print(f"  OK extracted core/constants.py contains VERSION={TEST_TAG}")

        assert exit_calls == [0]
        print(f"  OK sys.exit(0) called")

        # ── Phase 3: tampering rejections ───────────────────────────────────
        print("\n[3/4] Tampering rejection tests")

        # Bad SHA — should fail before extract
        try:
            updater.download_and_apply(
                info["zipball_url"],
                expected_sha256="0" * 64,
                expected_version=info["version"],
            )
            raise AssertionError("Bad SHA was accepted")
        except RuntimeError as e:
            assert "Integrity check failed" in str(e), str(e)
            print(f"  OK Bad SHA rejected:        {e}")

        # Downgrade attempt — should fail BEFORE download
        try:
            updater.download_and_apply(
                info["zipball_url"],
                expected_sha256=info["sha256"],
                expected_version="1.0.0",   # NOT greater than current 1.0.0
            )
            raise AssertionError("Downgrade was accepted")
        except RuntimeError as e:
            assert "Refusing to apply" in str(e), str(e)
            print(f"  OK Downgrade rejected:      {e}")

        # Bundled-VERSION mismatch (claim 2.0.0, zip contains 1.0.99)
        try:
            updater.download_and_apply(
                info["zipball_url"],
                expected_sha256=info["sha256"],
                expected_version="2.0.0",
            )
            raise AssertionError("Version-mismatch was accepted")
        except RuntimeError as e:
            assert "version mismatch" in str(e).lower() \
                or "verify update version" in str(e).lower(), str(e)
            print(f"  OK Bundled-VERSION mismatch rejected: {e}")

        # ── Phase 4: zip-slip rejection ─────────────────────────────────────
        print("\n[4/4] zip-slip rejection test")
        # Build a malicious zipball with a path-traversal entry
        evil_buf = io.BytesIO()
        with zipfile.ZipFile(evil_buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{TEST_ROOT}/core/constants.py", f'VERSION = "{TEST_TAG}"\n')
            z.writestr(f"{TEST_ROOT}/../../../escape.txt", "pwn3d")
        evil_bytes = evil_buf.getvalue()
        evil_sha   = hashlib.sha256(evil_bytes).hexdigest()

        # Re-serve the evil zipball
        nonlocal_zip = {"data": evil_bytes}
        class EvilHandler(BaseHTTPRequestHandler):
            def log_message(self, *_a): pass
            def do_GET(self):
                if self.path == "/releases/latest":
                    body = json.dumps(dict(release_json,
                                           body=f"sha256: {evil_sha}\n")).encode()
                    self.send_response(200)
                    self.send_header("Content-Type",   "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/zipball":
                    self.send_response(200)
                    self.send_header("Content-Type",   "application/zip")
                    self.send_header("Content-Length", str(len(nonlocal_zip["data"])))
                    self.end_headers()
                    self.wfile.write(nonlocal_zip["data"])
                else:
                    self.send_error(404)

        server.shutdown()
        server.server_close()
        evil_server = HTTPServer(("127.0.0.1", PORT), EvilHandler)
        threading.Thread(target=evil_server.serve_forever, daemon=True).start()

        try:
            updater.download_and_apply(
                f"http://127.0.0.1:{PORT}/zipball",
                expected_sha256=evil_sha,
                expected_version=TEST_TAG,
            )
            raise AssertionError("zip-slip path was extracted")
        except RuntimeError as e:
            msg = str(e).lower()
            assert "path-traversal" in msg or "absolute path" in msg, str(e)
            print(f"  OK zip-slip entry rejected: {e}")
        evil_server.shutdown()
        evil_server.server_close()

        print("\n" + "=" * 60)
        print("  ALL E2E CHECKS PASSED")
        print("=" * 60)

    finally:
        # Restore module state
        for k, v in saved.items():
            if k == "_cached":
                updater._cached.clear()
                updater._cached.update(v)
            else:
                setattr(updater, k, v)
        _sp.Popen = orig_popen
        sys.exit  = orig_exit

        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
        shutil.rmtree(tmp_app, ignore_errors=True)


if __name__ == "__main__":
    main()
