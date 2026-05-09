# Changelog

All notable changes to AlienCore are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-05-09

Initial public release.

### Added

- **Adaptive optimizer** — watches CPU, GPU, RAM, disk, and network activity in
  real time and switches between 7 built-in profiles (Idle, Light, Gaming,
  Streaming, Creative, Heavy, Boost) plus any custom profiles you define.
- **Floating sensor bar** — always-on-top HUD with live CPU / GPU / RAM / NVMe
  temps, clocks, fan RPM, network throughput, and animated sparklines. Fully
  resizable, dockable, and theme-aware.
- **Monitor loop** — sub-second workload detection with hysteresis so profiles
  don't flap under borderline load.
- **Hardware detection** — automatic CPU/GPU/RAM/storage fingerprinting with a
  cached profile; supports Intel, AMD, and NVIDIA platforms.
- **Settings GUI** — 17 tabs covering Display, CPU, GPU, RAM, Visual, Network,
  Storage, Privacy, Profiles, Custom Profiles, Service, Thresholds, AI,
  Insights, Drivers, About, and Account.
- **11 visual themes** — Void, Nebula, Ember, Aurora, Spectre, Crimson,
  Phantom, Solaris, Hex, Glacier, Venom.
- **AI Advisor & AI Chat** — optional multi-provider AI assistant (OpenAI,
  Anthropic, Google, local Ollama) with tool access for applying tweaks, with
  full scope preview and liability waiver for any high-risk action.
- **AI watchdog** — background AI loop that can suggest or auto-apply
  optimizations based on observed behavior.
- **Learning engine** — records which profiles work well for which app
  fingerprints and adapts over time.
- **Boost tracker** — monitors CPU boost headroom relative to your silicon's
  detected maximum frequency.
- **Windows service integration** — install/uninstall as a proper Windows
  service, or run foreground for testing.
- **Startup-with-Windows** sync (registry-based, toggleable).
- **Account system** — email + one-time PIN sign-in, 30-day free trial on
  first login, $19.99 lifetime base license, +$4.99 Pro add-on, purchases via
  PayPal with automatic license activation.
- **Source-available license** — full source visible to licensed buyers; not
  open source, no redistribution.
- **First-run welcome dialog** explaining the trial, licensing, and initial
  hardware scan.
- **Agentic AI safety** — any AI-initiated action that touches system state
  shows scope, damage warning, liability waiver, and (for RISK_HIGH tools) an
  explicit "I understand" checkbox.
- **Automatic updater** — polls GitHub releases on a 6-hour cadence, prompts
  for one-click update-and-restart, preserves your settings and session.
- **Developer YubiKey access** for signed builds (serial-pinned, in-memory
  session only, never persisted).

- **Hard-lock paywall on trial expiry.** Previously the trial-expired
  state was a soft-lock — basic monitoring, the sensor bar, and the
  baseline + profile tweaks kept running indefinitely; only ~14 advanced
  Settings panels (CPU TVB optimizer, GPU dynamic boost, RAM working-set
  trimmer, etc.) and the AI tab gated behind `_gate()` actually showed
  "trial expired" lock cards. A user who never paid could keep using
  AlienCore's core feature set forever.

  Now: whenever a user is signed in but their 30-day trial has elapsed
  and they haven't purchased a base license, AlienCore refuses to run.
  Launch shows a dedicated paywall window (`gui/paywall_dialog.py`) that
  replaces the normal startup entirely. The window offers:
  - Purchase the AlienCore base license ($19.99) → opens PayPal in the
    browser, polls `/auth/check` every 3 s for activation (3-minute
    timeout), then closes and lets startup continue once the IPN lands.
  - Pro $4.99 add-on shown but locked (Pro requires base ownership;
    cannot be reached from this dialog by definition).
  - Sign Out → clears the session and routes back through the login
    dialog so the user can authenticate with a different email that
    already owns a license.
  - Quit / window close → exits AlienCore. The paywall reappears on the
    next launch until purchase or sign-out resolves it.

  New `auth.needs_paywall()` is the single-source-of-truth gate
  condition: `is_logged_in() AND not is_on_trial() AND not is_licensed()
  AND trial_started_at is set`. The paywall is subprocessed via
  `aliencore.py --paywall` for the same Tk-isolation reason as the login
  dialog (a tk.Tk() destroyed in the main process before later spawning
  the bar/tray Tk roots crashes the Tcl C runtime on Windows).

  Dev YubiKey passthrough still works — a serial-allowlisted YubiKey
  detected by the paywall's poll thread activates the in-memory dev
  session and unlocks startup without payment, identical to how it
  works in the login dialog.

### Security

- All auth traffic flows over HTTPS to `aliencoreapp.duckdns.org`.
- Session tokens are stored locally under `%LOCALAPPDATA%\AlienCore\session.json`
  with atomic replace writes; a 72-hour offline grace period lets licensed
  users work without network access.
- Dev YubiKey serials are burned into the key's chip by Yubico and cannot
  be spoofed from source.

- **Pre-launch audit hardening (session 43).**
  - **Per-IP rate limit on `/auth/verify-pin`.** The per-PIN attempt
    cap (`PIN_MAX_ATTEMPTS = 5`) blocked single-IP brute force, but a
    botnet sharing many IPs could still combine to brute-force the
    6-digit PIN space. Added `pin_verify_attempts_by_ip` table tracking
    failed attempts per `(ip, day)`; rejects further verify requests
    from an IP after `PIN_VERIFY_PER_IP_DAILY_CAP = 50` failures in a
    UTC day. Failure response is the generic `_AUTH_FAILED` so the cap
    can't be probed for email enumeration.
  - **IPN `custom` email defensive normalization.** PayPal IPN handler
    now runs the buyer email from the `custom` field through
    `_normalize_email()` (lowercase + NFKC + strip-`+alias`) instead
    of just `strip().lower()`. The current client always sends an
    already-canonical email, so this is defense-in-depth — a future
    code change that fed a raw user-typed address into the PayPal URL
    would have created license rows on a non-canonical user with
    matching support tickets that were very hard to debug.
  - **Refund cascade on `AC_BASE`.** When PayPal issues a Base refund,
    the server now also clears `has_pro` (Pro depends on Base — leaving
    a refunded user with `has_pro=1, has_base=0` was visibly inconsistent
    state and let them keep using Pro features). Any matching
    `AC_PRO` purchase row is marked `revoked_with_base` for the audit
    trail.
  - **AI watchdog prompt-injection defense.** `core/ai_manager.py`
    sanitizes string fields in `get_system_context()` before they get
    JSON-serialized into the watchdog / chat prompt. Defense against a
    USB device or external sensor whose vendor-supplied name string
    contains backticks, triple-quotes, or newlines designed to break
    out of the JSON snapshot and inject instructions. Claude is robust
    to most of this in practice, but defense-in-depth.
  - **AI tool argument schema enforcement at dispatch.** Tool calls
    from the model now validate against the tool's `input_schema`
    before reaching the handler. A schema violation never executes —
    every current handler also validates its own args, but the contract
    layer means a future tool added without per-handler validation is
    still safe. Tools execute as Administrator, so this is a
    privilege-escalation hardening.

- **Reinstalling AlienCore could silently re-sign-in the previous user.**
  Symptom: uninstall AlienCore, delete `%LOCALAPPDATA%\AlienCore\`,
  reinstall, launch — the Account tab showed the same email signed in
  with the same trial countdown, no PIN prompt. Anyone with physical
  access to the machine could effectively impersonate the prior user
  by reinstalling.

  Root cause: `core/auth.py` stored `session.json` under
  `%APPDATA%\AlienCore\` (Roaming) while every other piece of writable
  user state — config, logs, hardware cache, update state — lived under
  `%LOCALAPPDATA%\AlienCore\` (Local). Wiping the Local folder did
  nothing to the Roaming session file, so `load_session()` read the
  stale token after reinstall and skipped the login dialog entirely.
  The fingerprint-bound trial countdown ride-along was a separate
  symptom of the same orphaned file (it caches `trial_started_at`).

  Fix in `core/auth.py`: `_SESSION_DIR` now points at `USER_DATA_DIR`
  so `session.json` lives next to `config.json` and friends. Wiping
  `%LOCALAPPDATA%\AlienCore\` now actually clears the cached session.
  A one-time `_migrate_legacy_session_if_needed()` runs at startup to
  move any pre-existing `session.json` from the old Roaming path into
  the new location, so users upgrading from earlier 1.0.x builds stay
  signed in across the upgrade.

  Trial-reset abuse remains blocked: trial start is still anchored to
  the hardware fingerprint server-side (`backend/server.py`), so a
  reinstall + sign-in correctly resumes the same trial clock from the
  server even though the local session is now wiped.

### Changed

- **`--no-elevate` argparse flag is now registered only in source mode.**
  Frozen builds enforce elevation via the `requireAdministrator`
  manifest, so the flag was a no-op on installed builds — but
  advertising it in `--help` was misleading and would have hidden a
  regression if a future build accidentally dropped `uac_admin=True`
  from the PyInstaller spec.

### Fixed

- **Sign Out from the trial-expired paywall exited AlienCore instead of
  returning to the login dialog.** Expected behavior was: clicking
  Sign Out clears the session and routes back through the login
  dialog so the user can re-authenticate with a different email that
  already owns a license. Actual behavior: the app exited and the
  user had to manually relaunch to see the login dialog again. Root
  cause was a stale in-memory session in the parent process: after
  the paywall subprocess deleted `session.json` via `auth.logout()`,
  the parent's `auth.load_session()` reload silently no-op'd because
  the file no longer existed — it left the parent's `_session` global
  holding the old token. `is_logged_in()` then returned True in the
  parent and the paywall loop's "fall back to login" branch never
  fired. Fix in `core/auth.py::load_session()`: now clears `_session`
  when the file is missing, making the function reload-safe across
  subprocess sign-out boundaries.

- **Trial-expired paywall window still cropped its Refresh License
  button below the visible area** after the first round of layout
  fixes. The "Already paid? Click Refresh after a moment, or sign
  out and back in with the email you used at checkout." help text
  wraps onto two lines and pushed the Refresh button off the bottom
  of the 780 px window. Bumped to 580×860 with a comfortable bottom
  margin so a future copy edit to the help text doesn't re-clip.

- **Trial-expired paywall window clipped its own text and hid its
  controls.** First paragraph lost the leading "P" of "Purchase…" off
  the left edge and trailed off the right; the YubiKey-detected status
  line clipped on the right. The Sign Out, Refresh License, and Quit
  buttons were missing entirely from the visible window — implemented in
  code, but rendered below the bottom edge. Root cause in
  `gui/paywall_dialog.py`: 520×660 window with internal frame padding
  left only ~420 px of horizontal content area, but several labels had
  `wraplength` set to 440–460; vertical content summed to ~744 px and
  overflowed the 660 px window. Bumped to 580×780 with corrected
  wraplengths (480 banner / 470 Pro / 510 status & footer help text).

- **YubiKey developer dev-unlock did not unlock the Settings or AI Chat
  windows.** Inserting the dev YubiKey while the trial-expiry paywall
  was open dismissed the paywall and started the app, but every gated
  Settings panel still rendered locked, the Account tab still showed
  "No active license", and the AI tab still showed the Pro paywall.
  Root cause: `try_dev_unlock()` is in-memory only by design (so pulling
  the key reverts on restart) — the paywall subprocess set its own
  `_session` to dev, but the Settings and AI Chat subprocesses each
  read `session.json` from disk on startup and only re-ran
  `try_dev_unlock()` when `is_logged_in()` was False. With a real
  trial-expired account signed in, the guard was always True so the
  dev unlock never fired in those subprocesses. Fix in `aliencore.py`:
  the Settings and AI Chat handlers now run `try_dev_unlock()` whenever
  `is_pro()` is False, which covers every state where dev unlock could
  rescue the user. Sync execution (instead of a background thread) so
  panel gates render with the correct license bits the first time —
  the 300 ms – 5 s PowerShell cost is invisible during the prewarmed
  Settings path.

- **CPU / GPU / RAM Settings tabs rendered 4–6 stacked copies of the
  same trial-expired lock card** when the user was past their 30-day
  trial without a license. `_gate()` in `gui/settings_gui.py` packs one
  full lock frame per gated feature, and these tabs each call `_gate()`
  4–6 times for features that all gate behind the same Base license, so
  every feature got its own identical "Buy AlienCore $19.99" banner.
  Fix introduces `_gate_group()` which renders all panels if every
  feature unlocks, or one shared lock card if any are locked. The CPU,
  GPU, and RAM tab builders now call `_gate_group` once instead of
  `_gate` per feature.

- **AlienCore crashed silently after the first PIN sign-in on a fresh
  install.** Symptom: install → launch → enter email → enter PIN →
  "Signed in!" → window vanishes, no traceback in the log, no error
  dialog. Re-launching from the desktop shortcut worked because the
  cached `session.json` skipped the login flow entirely.

  Root cause: the first-launch sign-in path in `aliencore.py::_show_login`
  imported `gui.login_dialog` and called `tk.Tk().mainloop()` directly in
  the main process, then destroyed that root after the user signed in.
  The subsequent `tk.Tk()` roots that `gui/bar.py` (daemon thread) and
  `gui/tray.py` (main thread) build for the sensor bar and tray menu
  then crashed inside the Tcl/Tk C runtime — recreating a Tk root in a
  process that had already destroyed one is undefined on Windows and
  produces no Python traceback. The Settings → Sign In path was already
  fixed in session 40 by spawning `AlienCore.exe --login` as a
  subprocess; the first-launch path was missed.

  Fix in `aliencore.py::_show_login`: spawn the login dialog as a
  `--login` subprocess (same pattern as `settings_gui._open_login`
  and `tray._open_ai_chat`). After the subprocess exits, reload
  `session.json` from disk so the parent picks up the new session.
  Also re-runs `try_dev_unlock()` in case the user plugged a dev
  YubiKey in during the dialog (in-memory dev sessions don't carry
  across processes).

- **Inno Setup post-install Launch failed with "CreateProcess failed;
  code 740. The requested operation requires elevation."** After
  flipping the manifest to `requireAdministrator`, the installer's
  `[Run]` step couldn't spawn `AlienCore.exe` because Inno's `[Run]`
  drops back to the original (non-elevated) user identity by default,
  even when the installer itself is admin. Fix in
  `installer/aliencore.iss`: add `runascurrentuser` to the `[Run]`
  flag list so the spawn keeps Inno's elevated token.

- **AlienCore.exe could fall through to non-admin operation if the user
  declined UAC.** `aliencore.spec` shipped with `uac_admin=False`,
  delegating elevation to `core/elevation.relaunch_as_admin()`. If the
  user clicked "No" on the UAC prompt, the app continued running as a
  standard user — sensors that need kernel access (CPU MSR temps,
  SMBus DIMM temps, AWCC WMI) silently returned `---`, leaving the
  app visibly "running" but functionally broken. `aliencore.spec` is
  now `uac_admin=True`, so Windows enforces admin at process creation
  and "No" on UAC means the process does not start at all. The
  `(Debug)` Start Menu shortcut was removed from `installer/aliencore.iss`
  since `--no-elevate` is now a no-op in frozen builds.

- **AWCC Service tab status froze on "Installed (WMI offline)" even
  after the WMI connection completed.** Although session 40's fix made
  AWCC query on the first sensor poll, the Settings prewarm builds the
  Service tab ~400 ms after open while the first AWCC poll lands ~3 s
  in — the label captured the stale `False` default and never
  re-evaluated. Fix in `gui/settings_gui.py::_hw_panel`: the AWCC
  label widget reference is now retained, and a 1-second poll (max 60
  attempts) updates it to "WMI connected" the moment `awcc_available`
  flips True, then stops polling.

- **Settings could not be opened from a freshly-launched AlienCore after a
  prior run died unexpectedly.** When the parent AlienCore.exe crashed or
  was force-quit, its hidden prewarmed Settings subprocess survived as an
  orphan, blocked INFINITE on a "show" event that would never come from a
  dead parent. The orphan held the `AlienCore_Settings_v1` mutex, so every
  subsequent Settings spawn — from the next launch's tray prewarm or a
  manual `--settings` invocation — collided with the orphan's mutex and
  silently exited within ~1 second. Symptom: clicking the tray's Settings
  entry visibly did nothing, no error in the log.

  Fix in `gui/settings_gui.py::_start_show_event_watcher`: the prewarm now
  records its parent PID via `os.getppid()` and waits on
  `WaitForMultipleObjects(show_event, parent_handle)`. If the parent dies
  first, the prewarm calls `root.destroy()` cleanly, releasing the mutex,
  so the next AlienCore launch can spawn a fresh prewarm. `open_settings`
  also gained explicit lifecycle logging (entered → mutex check → root
  created → mainloop) so future silent-exit issues are diagnosable from
  the log alone.

- **Sign In button did nothing on installed builds.** The Sign In handler
  in the Settings Account tab (and per-feature lock cards) spawned the
  login dialog as `subprocess.Popen([sys.executable, aliencore.py, "--login"])`
  — but in a frozen build, `sys.executable` is `AlienCore.exe` and there's
  no `aliencore.py` next to it, so argparse rejected the unknown positional
  arg and the windowed (no-console) process died silently. Fix in
  `gui/settings_gui.py::_open_login` and `gui/tray.py::_open_ai_chat`:
  branch on `sys.frozen` and pass `[sys.executable, "--login"]` directly
  in frozen mode, mirroring the tray's existing `_settings_argv` logic.

- **`relaunch_as_admin()` was broken in frozen builds.** Same root cause:
  it forwarded `sys.argv[0]` as a parameter, which on a frozen build
  duplicates the exe path (`AlienCore.exe AlienCore.exe`). The elevated
  copy died silently on argparse, leaving the user with a UAC prompt that
  produced "nothing." `core/elevation.py::relaunch_as_admin` now branches
  on `sys.frozen` and forwards only `sys.argv[1:]` for frozen builds.

- **AWCC live status in the Service tab incorrectly showed "Installed
  (WMI offline)" for the first ~15 seconds after Settings opened.**
  `core/sensors.py::_read_awcc_data` incremented its throttle counter
  *before* the cycle check, so the first AWCC query didn't run until poll
  #5 (~15 s after `sensors.start()`). The Settings prewarm built the
  Service tab well before that, capturing the stale `awcc_available=False`
  default. Now queries on poll #1 and every 5 cycles thereafter.

- **Account tab in Settings did not refresh after a successful Sign In or
  background YubiKey dev-unlock.** `_GATED_TAB_LABELS` only listed
  `{CPU, GPU, RAM, AI}`, so `_rebuild_gated_tabs` skipped the Account
  panel. Added "Account" to the set so the "Not signed in" header,
  license badge, and action buttons all flip to the post-sign-in state
  the moment auth flips.

- **Installed builds crashed on first launch with `PermissionError [Errno 13]`
  trying to write `config.json` inside `C:\Program Files\AlienCore\`.** The
  frozen build resolved every writable path (config, logs, hardware cache,
  history files, `update_state.json`) relative to the install directory,
  which Windows protects from non-admin writes. Reproduced on a fresh test
  install where the desktop shortcut launches without elevation.

  Fix splits the path roots in `core/constants.py`:
  - `BASE_DIR` keeps pointing at the install dir for read-only bundled
    assets (`lhm_bridge.exe`, `CHANGELOG.md`, `LICENSE`, `docs/manual.html`).
  - New `USER_DATA_DIR` resolves to `%LOCALAPPDATA%\AlienCore\` when frozen
    (project root in source mode so the dev workflow keeps working). All
    writable state — `config.json`, `logs/`, `learning.json`, `hardware_profile.json`,
    `update_state.json`, profile dir, throttle/efficiency/boost/pagefile
    history — now lives there. Survives reinstalls; the Inno Setup uninstaller
    explicitly does not touch it.

  `os.makedirs(USER_DATA_DIR)` runs once at module import with a `%TEMP%`
  fallback so a corrupt or locked-down user profile surfaces a clear
  PermissionError on first write instead of an opaque `ImportError` chain.

- **Frozen-build path resolution audit — five modules computed paths from
  `__file__`, which resolves inside the PyInstaller bundle archive instead
  of next to `AlienCore.exe`.** All five would have failed silently or
  loudly in the installed build:
  - `core/lhm_manager.py` couldn't find `lhm_bridge.exe` (sensor bar would
    have stayed `---` forever).
  - `core/elevation.py` passed a non-existent `cwd` to `ShellExecuteW` and
    the elevated-task safety check ran against a virtual path.
  - `core/ai_tools.py::_h_open_settings` and
    `gui/settings_gui.py::_open_chat` tried to spawn a non-existent
    `aliencore.py` (AI chat & "Open Settings" tool calls would have died).
  - `gui/settings_gui.py::_open_manual` looked for `docs/manual.html` next
    to the .exe instead of inside `_internal/docs/` where PyInstaller puts
    bundled data files.

  All five now use `core.constants.BASE_DIR` (which is `sys.executable`-aware)
  for bundled assets, `sys.executable` directly for subprocess relaunches,
  and `getattr(sys, "_MEIPASS", BASE_DIR)` for PyInstaller-bundled data.

- **Settings tray subprocess relaunch in `aliencore.py`** was passing
  `aliencore.py` as a script argument, which doesn't exist on disk in a
  frozen build. Frozen branch now launches `[sys.executable, "--settings"]`
  directly so the bundle re-enters via its own embedded entry point.

### Security

- **`lhm.bridge_exe` config override is no longer honored unless
  `ALIENCORE_DEV_BRIDGE_OVERRIDE=1` is set in the environment.** Previously
  any path written into `config.json` would be spawned as the lhm_bridge
  subprocess, and AlienCore runs as admin — so a config-file tamper became
  an admin code-execution primitive. Production installs now always use
  the bundled `lhm_bridge.exe`; developers building a custom bridge opt
  in via the env var.

### Changed

- **Inno Setup uninstaller now removes the `AlienCoreElevatedStartup`
  scheduled task** via a new `[UninstallRun]` entry in
  `installer/aliencore.iss`. Previously the task survived uninstall and
  pointed at a deleted `C:\Program Files\AlienCore\AlienCore.exe`,
  failing silently on every logon and cluttering Task Scheduler.

- Inno Setup `[UninstallDelete]` comment in `installer/aliencore.iss`
  updated to reflect that user state lives in `%LOCALAPPDATA%\AlienCore\`
  and survives uninstall/reinstall cycles untouched.

### Added

- **AlienCore now ships as a standalone Windows installer.** Download
  `AlienCore-<version>-Setup.exe` from the [GitHub releases page](https://github.com/mourninggrace/AlienCore/releases/latest),
  double-click, accept the SmartScreen warning, and the installer drops
  the app into `Program Files\AlienCore\`, creates Start Menu and
  Desktop shortcuts, and offers to launch on completion.  No need to
  install Python, no `install_deps.py` to run.  The app self-elevates
  via UAC on first launch (required for kernel MSR / WMI / SMBus
  access) and registers its silent auto-start Task Scheduler entry on
  first run.  See `installer/README.md` for the build pipeline
  (PyInstaller onedir + Inno Setup 6).

  *Note: the v1.0 installer is unsigned.*  Windows SmartScreen will
  show a "Windows protected your PC" warning on download — click
  **More info** → **Run anyway** to proceed.  The installer's
  SHA-256 digest is published in each GitHub release's notes for users
  who want to verify it independently.  A signed build is planned for a
  future release once initial demand justifies the certificate cost.

### Changed

- **In-app updater on installed builds now opens the GitHub releases
  page in your default browser** instead of attempting an in-place
  source overlay.  The old robocopy-based updater can't update a
  PyInstaller-frozen install (the install directory is `aliencore.exe`
  + `_internal/`, not loose `.py` files), so on installed builds the
  update dialog's primary button now reads **Download New Installer**
  and clicking it opens the latest release page where you grab the
  fresh `Setup.exe`.  Source-run installations (running
  `python aliencore.py` from a clone) keep the existing seamless
  source-overlay auto-update behavior.
- README's Install section restructured: "Recommended — Windows
  installer" now leads, with the source / developer path moved to
  "Alternative".

### Security / Audit

- **Pre-launch security and stability audit** — 25 findings from a
  four-agent review across the backend, client auth/license/updater,
  privilege/subprocess surfaces, and runtime hot paths.  All 25 fixed.
  Headlines below; full file:line breakdown in the audit memo.

  *License integrity (Ed25519 signing).*  The backend now signs every
  `/auth/check` and `/auth/verify-pin` response with an Ed25519 private
  key the client never sees.  The client refuses to grant `has_base` or
  `has_pro` without a valid signature over the canonical license payload
  (`email|has_base|has_pro|trial|exp|issued|signed|fingerprint`).
  Closes the prior MITM-flip-`has_pro`-to-true vector — even an attacker
  who can rewrite responses on the wire can no longer forge a Pro grant
  because they don't have the signing key.  See
  `tools/generate_license_keypair.py` for the one-shot deploy keygen.

  *Trial farm closed.*  Verify-PIN now refuses empty / `unknown`
  fingerprints (was previously a free fresh-trial bypass for sandboxed
  VMs).  Emails are NFKC-normalized and `+tag` aliases are canonicalized
  server-side so `me+1@gmail.com` no longer earns a separate trial.
  When a fingerprint already burned a trial, every subsequent email on
  that hardware inherits the original start date.

  *PIN endpoint hardening.*  Per-email cooldown (60 s) and per-IP daily
  cap (30) on `/auth/send-pin` shut down the open-spam-relay vector.
  PINs are now compared with `secrets.compare_digest`, fail counter
  burns the row at 5 wrong attempts, and every failure path returns the
  same generic "Invalid email or PIN" message — no more email-existence
  oracle.  Server-side error responses no longer leak Brevo / urllib
  exception text back to the client.

  *Session lifetime + binding.*  Sessions now carry an immutable
  `issued_at`; rolling extension can no longer push the absolute deadline
  past 90 days, so a stolen token can't be kept alive forever.  The
  lazy-fingerprint-bind path was removed — every session is bound at
  `/auth/verify-pin` and any unbound session is rejected on next check.

  *Updater zip-slip + downgrade prevention.*  Update zip extraction now
  iterates `ZipInfo` manually, rejects absolute paths / `..` segments /
  symlinks, and verifies every member resolves under the extract dir.
  `download_and_apply` re-checks the version at apply time AND extracts
  the bundled `core/constants.py` to confirm its `VERSION` matches the
  release tag — defeats both pointer-swap and downgrade-by-tag attacks
  even if a maintainer release token leaks.

  *PATH-hijack hardening.*  `core/fingerprint.py` and `core/auth.py`
  invoke `wmic.exe` / `powershell.exe` via absolute `%SystemRoot%\
  System32\…` paths.  Bonus: PowerShell `Get-CimInstance` is now the
  primary BIOS-UUID source so AlienCore keeps working when Microsoft
  finishes removing WMIC in Windows 11 24H2.

  *Elevated-task LPE defense.*  `core/elevation.py` refuses to install
  the `AlienCoreElevatedStartup` Task Scheduler entry when either the
  Python interpreter or the install directory lives in a user-writable
  location (per-user `Programs\Python`, project on Desktop, etc.).
  Set `ALIENCORE_ALLOW_USER_INSTALL=1` to override during development;
  the .exe installer puts AlienCore under `Program Files` so this is
  silent in production.  `relaunch_as_admin` now uses
  `subprocess.list2cmdline` instead of naive `f'"{p}"'` quoting and
  rejects argv elements with embedded control chars.

  *VBS / registry launcher hardening.*  `core/startup.py` `_write_vbs()`
  refuses to write a launcher whose interpreter, script, or base-dir
  path contains `"`, `'`, `\r`, `\n`, or `NUL`.  Same guard on the HKCU
  Run command.

  *Local stability.*  `config_manager.get()` now returns a true deepcopy
  (was returning the live cache, letting GUI / advisor mutations corrupt
  shared state until the next save).  `hardware_profile.json` and
  `update_state.json` writes are now atomic (`tmp` + `os.replace`) so a
  power-cut can't truncate them.  `core/sensors.py` parsers use `.get()`
  defensively — a single malformed LHM JSON line no longer freezes the
  bar to `---` until restart.  `gui/ai_chat.py::_confirm_tool` has a
  120 s timeout and a `winfo_exists` guard so a closed chat window
  during a tool dispatch can no longer hang the API worker thread
  indefinitely.  `learning.py` event appends are now buffered and
  flushed every 5 minutes (was rewriting the entire multi-MB JSON on
  every thermal/profile event).

  *Service-manager lockdown.*  `services_manager.set_startup_type` /
  `start_service` / `stop_service` now validate `service_name` against
  the curated list before invoking `sc.exe`.  Future AI tool exposures
  (e.g. an LLM-driven manage-service) can't reach uncurated services
  like `Winmgmt` or `RpcSs`.

  *Hygiene cleanup.*  Sensor cells share the same sparkline ring-buffer
  size (mem-tier-scaled, was hardcoded `90`).  `_reg_set` raises on
  unknown hive prefix instead of silently writing to HKLM.  `expires_at`
  rejects `NaN`/`inf` (was extending offline grace forever).  Legacy
  unsigned `session.json` files are no longer accepted (v1 ships with
  HMAC-required from day one).  `/health` no longer leaks the version
  string.  Settings-window worker callbacks check `winfo_exists()`
  before scheduling on root.  `_show_event` HANDLE is closed at quit.

### Removed

- **Windows-service install path removed.** `aliencore.py --install` and
  `--uninstall` flags, plus the `_install_service()` / `_uninstall_service()`
  functions that registered an `AlienCoreService` entry via `sc.exe`, are
  gone. The service path was structurally broken — it pointed `binPath` at
  `python.exe aliencore.py`, which never calls `StartServiceCtrlDispatcher`,
  so Windows always killed the process with **Error 1053** (service didn't
  respond to start in time). Even if that were fixed, services run in
  Session 0 where the sensor bar and tray icon can't render. AlienCore's
  real auto-start path has always been the `AlienCoreElevatedStartup` Task
  Scheduler entry installed by `core/elevation.py` at first elevated launch
  — silent, elevated, runs in the user session, fully working.
  Users who previously ran `aliencore.py --install` should remove the
  orphaned service with `sc delete AlienCoreService` from an elevated
  terminal. README and user manual updated accordingly.

### Security / Legal

- **Session is now bound to the machine that created it.** Three-layer
  defense added against the "copy `session.json` to another PC" piracy
  vector. (1) Client sends the hardware fingerprint on every
  `/auth/check` call, not just at PIN-verify time. (2) Server stores the
  fingerprint in a new `sessions.fingerprint` column at login, and on
  every subsequent check rejects the token with
  *"Session is bound to another machine."* if the incoming fingerprint
  doesn't match. Sessions that were created with no fingerprint
  (detection failure / older clients) lazy-bind to the first real
  fingerprint that checks in — closing the "log in from a sandboxed VM
  to get an unbound token" bypass. (3) `session.json` itself is now
  HMAC-signed with a key derived from the machine fingerprint, so
  tampering with `has_base` / `has_pro` / `expires_at` in a text editor
  is detected on load and the session is discarded. Legacy unsigned
  session files are still accepted once and re-signed on next persist.
- **Auto-update integrity check**: AlienCore now refuses to apply an
  update unless the GitHub release notes include a
  `sha256: <64-hex-digest>` line matching the downloaded zip. Closes the
  unsigned-update RCE path (a tampered zip arriving via DNS poisoning
  or compromised release would previously have been extracted and run
  with elevated privileges). Each future release MUST include the
  digest in its body — generate with
  `certutil -hashfile <zip> SHA256`.
- **Backend hard-exits at startup if `AC_SECRET` is the default**
  (`CHANGE_ME_IN_PRODUCTION`) and `AC_PAYPAL_MODE=live`. Sandbox mode
  still warns but allows the default for local dev. Stops the server
  ever shipping with a publicly-known secret protecting any future
  admin endpoint.
- **PayPal IPN now rejects unknown `item_number` values** with HTTP 400
  instead of silently inserting a `status="completed"` purchase row.
  Previously a crafted IPN with `item_number="AC_FREEBIE"` and
  `mc_gross=0.01` would skip the price-validation guard (which was
  gated by `if product and ...`) and pollute the audit trail without
  granting a license.
- **PowerShell injection hardened in pagefile tweak.** The
  `ram.pagefile_custom_mb` value from `config.json` is now hard-cast to
  `int` and bounds-checked (1 ≤ mb ≤ 65536) before it ever touches the
  PowerShell command string. A user-writable config file can no longer
  be used as an LPE vector against the elevated AlienCore process.
- Added a prominent **trademark disclaimer** to the README (top callout +
  footer notice) clarifying that AlienCore is an independent third-party
  utility and is **not affiliated with, endorsed by, sponsored by, or
  connected to Dell Technologies Inc. or Alienware**. Covers both the
  "Alien" naming and the AWCC WMI integration. This is now the most
  visible piece of legal text in the repo, sitting directly under the
  pre-launch waitlist callout.
- **Pro license can no longer be purchased without an active Base
  license.** Three-layer enforcement:
  1. Settings → Account: the Pro card now shows a disabled "Locked ✕"
     button and an inline note explaining Base must be purchased first.
  2. The PayPal launcher (`_paypal()` in `gui/settings_gui.py`)
     short-circuits with a warning before opening the checkout URL if
     `auth.is_licensed()` is false and the requested product is AC_PRO.
  3. Backend `/paypal/ipn` handler refuses to grant Pro to users without
     `has_base=1` — the purchase row is recorded with status
     `rejected_no_base` and a WARNING is logged for manual refund. This
     blocks bypass attempts via hand-crafted PayPal links that skip the
     client UI entirely.

### Added

- **Sensor bar now shows every storage drive with per-type labels.** Old:
  hardcoded `NVM1` / `NVM2` slots only — SATA SSDs and HDDs were silently
  dropped because the parser filtered on the NVMe-only `Composite Temperature`
  sensor name. New: parser enumerates all drives LHM exposes under
  `hardwareType=="Storage"` and classifies each one via Windows
  `Get-PhysicalDisk` (`MediaType` + `BusType`), returning three lists —
  `nvme_temps`, `ssd_temps`, `hdd_temps`. The bar renders the appropriate
  cells (`NVM1`–`NVM4`, `SSD1`–`SSD4`, `HDD1`–`HDD2`) with one toggle per
  slot in Settings → Display, and auto-hides slots above the detected count
  using the Windows-reported drive set so cells appear on the bar's first
  paint instead of materializing 4–5 s later when the first valid LHM
  Storage update arrives. Per-type thresholds: NVMe & SATA SSD warn 60 / crit
  70 °C; HDD warn 45 / crit 55 °C. New module `core/storage_info.py` caches
  the Windows drive map for the process lifetime.
- **Inline cell mini-charts and 90-second history popups now populate for
  every storage cell** (extra NVMe slots, SSDs, HDDs). The numeric extractor
  was hardcoded to slots 0/1 of `nvme_temps`; replaced with a slot table that
  maps every storage key to its `(list_key, index)` pair so per-cell history
  feeds are populated end-to-end.
- 90-second sample popup window now inherits the sensor bar's transparency
  setting. The popup is a separate Tk Toplevel that previously rendered
  fully opaque regardless of `display.overlay_opacity`; it now reads the
  same value at open and re-applies it on each draw tick so the Settings
  slider adjusts both surfaces live.
- Settings → AI → Model dropdowns now **fetch the live model list directly
  from the configured provider** instead of showing a hardcoded list. New
  Anthropic flagships (Opus 4.8, future Claude tiers) and new OpenAI / Groq
  / Together / Mistral models surface in the dropdown automatically the
  moment the provider lists them at `/v1/models` — no AlienCore update
  required. Implementation: new `core/ai_models.py` calls each provider's
  `/v1/models` endpoint with a 1-hour cache, falls back silently to the
  built-in list whenever the key is missing, the network is unreachable,
  or the response is malformed. Anthropic results are tier-bucketed
  (opus → sonnet → haiku, newest version first within each tier); OpenAI
  native is filtered to chat-class IDs (gpt-*, o1/o3/o4-*) so the dropdown
  isn't polluted with embedding/whisper/dall-e/tts model names; third-party
  OpenAI-compat endpoints (Groq, Together, Ollama, etc.) show whatever the
  provider returns. A small **↻ Refresh** button next to the Model row
  forces a re-fetch — useful right after entering an API key, since the
  tab-open fetch happens before the key field is filled.
- New **Working** profile — a fourth default tier sitting between Idle and
  Gaming for productivity / multi-tasking workloads (many browser tabs, IDE
  + Slack + video call, Photoshop, etc.). Behavior is *responsiveness-tuned*,
  not throughput-capped: CPU ceiling stays at 100% so bursts get full power
  on demand, but the perf-increase threshold is raised to 30% (vs Gaming's
  15%, Idle's 50%) and core parking holds half the cores online so context
  switches don't pay the wake penalty. AWCC thermal profile stays Balanced
  (no GPU heat). Triggered primarily by *load* — sustained CPU% without
  gaming-class GPU heat — with a **process bias**: detecting browsers,
  Office, Teams/Slack/Zoom, IDEs, or Adobe apps lowers the CPU threshold by
  5pp so promotion happens sooner. The default 25% CPU threshold also
  **scales by hardware tier** via `mem_tier`: weaker hardware (≤ 8 GB RAM)
  promotes to Working at ~17% CPU, powerful hardware (> 32 GB) holds idle
  longer until ~29%. Hysteresis is longer than Gaming's (~50-60s vs ~30s)
  because productivity load is noisier (background indexing, AV scans, JS
  GC). Working appears in the tray override menu, the sensor bar's
  right-click menu, and as a base behavior option for Custom Profiles. New
  `profiles.working_cpu_threshold` and `profiles.custom_working_processes`
  config knobs surface in Settings → Profiles.
- The Settings theme now also retints the floating sensor bar.  Picking
  Crimson / Aurora / Hex / Glacier / etc. immediately reskins the bar's
  background, cell faces, outline, and label colors to match — both
  surfaces stay visually in sync.  The theme is persisted to disk the
  moment it's chosen so the bar (which runs in a separate subprocess from
  Settings) picks it up on its next config-mtime poll.
- New `core.mem_tier` helper scales AlienCore's in-memory caches and history
  buffers to installed RAM (low ≤ 8 GB, normal ≤ 16 GB, high ≤ 32 GB,
  xl > 32 GB).  On RAM-rich systems AlienCore keeps proportionally more data
  resident so the app feels snappier and sparklines show longer history:
  - Sensor bar sparkline/inline-chart ring buffers grow from 90/120 samples
    up to 360/480 (x4) on 64 GB+ systems.
  - LHM stale-cache window grows from 30 s up to 120 s, so the sensor bar
    keeps reading right through longer bridge hiccups under heavy load.
  - Boost-tracker sample history grows from 500 up to 2,000 samples.
  - Drivers tab now caches the PowerShell WMI query result at process level
    with a tier-scaled TTL (2 min → up to 8 min) — reopening Settings no
    longer re-runs the 3-5 s driver scan.
- README now shows a "Coming Soon — Launch Imminent" notice with a waitlist
  email so early repo visitors know v1.0.0 ships very soon.
- Settings → Profiles now includes a "Pick running app…" button next to each
  custom-process list, plus a "Pick" button in the custom-profile dialog —
  choose from currently running EXEs via a filterable, checkbox list
  instead of hand-typing process names.
- Settings → Profiles → Custom Profiles dialog: tray color is now picked with
  a real color swatch + color picker (old hex-only text field moved to an
  Advanced collapsible with the internal slug name and priority).
- Settings → Service → Windows Services Manager: each row now has explicit
  Start / Stop buttons (disabled based on current state), plus a pending-
  changes model — dropdown changes queue up and are only written when you
  press "Apply Pending Changes (N)". Missing admin rights are announced
  with an inline banner and failures surface a dialog listing which
  services didn't update.
- Feedback window now sends directly through the AlienCore backend
  (Brevo relay) instead of opening a mailto link — replies come back to the
  signed-in email (or whichever address you enter). Falls back to mailto
  if the backend is unreachable.
- Copy Report now includes Python version, Windows edition + UBR build,
  admin status, driver versions, signed-in account, active config flags,
  and the last 60 log lines (previously 40) — enough for remote debugging
  without needing a follow-up round trip.

### Changed

- Settings → Services: clicking Start on an already-running service or
  Stop on an already-stopped service is now a silent no-op instead of
  surfacing an `sc.exe` error dialog. The handler checks live state
  before invoking sc, so stale button states (e.g., after the service
  was changed externally) no longer pop an error.
- Settings → Profiles and Custom Profiles are now a single tab. Custom
  Profiles lives at the bottom under "App-Based Profile Switching".
- Settings → AI → Model section replaces the bare text entries with
  editable comboboxes that preset the right models for the selected
  provider (Anthropic vs OpenAI-compatible).
- Settings → Account is now a polished card layout. The status panel has
  a thin accent strip on top, a colored avatar circle with the user's
  initials, the email + tier subtitle, and a row of license badges
  (colored chips for Base License / Pro Add-on / Trial). For users who
  own everything, a "Your AlienCore Pro Subscription" panel fills the
  space below with a thank-you note and a bullet list of unlocked
  features — no more bare empty space where the purchase advertising
  used to live. The Licenses & Add-ons advertising still shows for
  users who haven't purchased everything yet.
- Settings → About Platform now shows the full Windows 11 edition + build
  (e.g. "Windows 11 Professional (build 26200.8246)") instead of the bare
  "Windows 10.0.26200" string.
- **Settings window now opens instantly, every time.** The tray pre-warms
  a hidden settings subprocess shortly after boot — full Python interpreter,
  AlienCore imports, Tk root, notebook, and first tab are all built in
  advance. Clicking "Open Settings" sends a Windows named-event signal
  (`AlienCore_Settings_Show_v1`) that the prewarmed process unblocks on,
  then deiconifies — sub-millisecond from click to visible. After the user
  closes settings the tray spawns a fresh prewarm in the background for the
  next click. Cold-spawn fallback kicks in if the prewarm crashed or the
  user clicks during the 2 s startup grace period. Cost: ~40-60 MB resident
  for the idle prewarm subprocess; the prior per-click cost (process spawn
  + interpreter init + module imports + Tk construction, ~1-2 s on cold
  launches) is now paid once at startup. Also fixed: `CreateEventW` /
  `OpenEventW` `HANDLE` returns now use `restype=c_void_p` so the 64-bit
  handle isn't truncated to 32 bits before reaching `SetEvent` /
  `WaitForSingleObject`.
- Settings window hides itself during construction and reveals once fully
  built — removes the half-drawn flicker on cold launches.
- Default `service.hardware_refresh_on_startup` is now False (was True) so
  first-boot doesn't eat an extra ~3s before the tray is ready.
- Default `ai.chat_history_max` bumped from 20 to 60 messages.
- Default sensor bar opacity lowered from 85% to 75%.
- Settings → CPU tab now shows both base and boost frequency when they
  differ.  WMI and psutil both report the CPU's base clock (e.g. 2,200 MHz
  for the i9-14900HX) as "max", which was misleading on any machine
  capable of Turbo Boost.  A built-in lookup table covers Intel 12/13/14th
  gen + Core Ultra and AMD Ryzen 5000/7000/9000 — the i9-14900HX now shows
  Base 2,200 MHz / Max boost 5,800 MHz.  CPUs not in the table fall back
  to the highest per-core clock LHM has ever observed on this machine.

### Changed

- Settings → Service: per-row Start/Stop now gives clear visible (and on
  failure, audible) confirmation.  The status banner is bigger and bolder,
  prefixes ✓ / ✗, color-codes green for success / red for failure, briefly
  flashes the affected row's background, beeps on failure, and auto-clears
  after a few seconds.  Bulk "Apply Pending Changes" / "Apply All Safe
  Recommendations" use the same banner.

### Fixed

- **Settings → Display → CPU temperature mode now actually works.** The
  Average / Per-core radio buttons wrote to config but no code ever read
  the value, so the bar always showed package average regardless of the
  setting. The bar's CPU cell, inline mini-chart, 90-second sparkline,
  and hover tooltip now all honor the mode. Per-core surfaces the
  hottest individual core at each tick — useful for catching single-core
  spikes that the package sensor smooths over (one P-core hitting 95 °C
  while package reads 78 °C is exactly the case where you want to see
  the spike). Falls back to package average when per-core data is
  unavailable (e.g. AMD SMU fallback path on locked systems).
- **Settings theme picker — Save button now flips to "Save" on theme
  change.** Previously the button stayed as "Close" because the picker
  pre-updated the dirty-check baseline before the comparison ran.
  Removed the redundant baseline write; the bottom-right button now
  reflects in-session theme changes the same way it reflects checkbox
  and radio changes.
- **Settings theme picker — Display tab no longer scrolls back to the
  top after each theme change.** The theme rebuild destroys and recreates
  every tab widget, but didn't snapshot the per-tab Canvas yview before
  teardown.  Added a `_tab_canvases` registry populated by `_make_tab`,
  with snapshot-and-restore in `_rebuild_for_theme` (deferred via
  `after_idle` so the inner-frame `<Configure>` binding has time to
  update the new canvas's scrollregion before `yview_moveto` runs).
  Active tab keeps its scroll position; inactive tabs reset to top
  (which is the existing behaviour for any tab the user hasn't visited
  yet).
- **Sensor bar now populates near-instantly at startup** instead of showing
  `---` for several seconds. Two changes work together: (1) `lhm_manager`
  now spawns the `lhm_bridge.exe` .NET subprocess on a background prewarm
  thread very early in startup (right after auth, before hardware
  fingerprint), and runs six discarded warmup polls so the bridge's 1-in-3
  Storage update stride lands two `.Update()` cycles — long enough for
  NVMe SMART async reads to populate before the SensorThread's first real
  poll. (2) `sensors.start()` was moved earlier in the startup sequence
  (right after boost-tracker config, before tweak application) so the
  SensorThread's first poll runs in parallel with `tweaks.apply_baseline` /
  `apply_profile` instead of after them. (3) The `time.sleep(2.0)` after
  bridge spawn was bumped to 3.0 s so `computer.Open()` reliably finishes
  enumerating multiple NVMe controllers + DIMM SPD on systems with many
  sensors. (4) The AMD-SMU CPU temp fallback no longer waits for three
  zero-temp polls before firing — with the new prewarm there's no need to
  give LHM nine extra seconds to "warm up".
- **Inline mini-charts and 90-second sparklines no longer collapse stable
  readings into a flat line glued to the bottom of the chart.** Both
  charts used `span = max(mx - mn, 0.5)`, which mapped every sample of a
  near-constant value to `y = y1` (the chart's bottom edge). Storage
  temps and idle RAM% looked completely dead. New behavior: when
  `mx - mn < 1.0`, force a 2-unit span centered on the current value
  range so stable readings draw down the middle of the chart and small
  fluctuations are visible above and below center.
- **Refresh License showed "Cannot reach server" for any non-200
  response.** `urllib.error.HTTPError` is a subclass of
  `urllib.error.URLError`, so the existing `except URLError` block in
  `core/auth.py` was swallowing every server rejection (401 expired
  token, 401 fingerprint mismatch, 400 bad request) and presenting them
  all as a generic network failure. `_post()` now catches `HTTPError`
  separately, reads the JSON error body, and surfaces the actual server
  message — so users see "Session expired. Please sign in again."
  instead of being told their connection is down.
- **Storage tab classified SATA SSDs as HDD** when the drive's WMI
  model name didn't contain the literal string "SSD" / "NVMe" / "Solid"
  (e.g., ADATA SX900, certain Crucial / Kingston / WD Blue SATA SSDs).
  `_get_drive_info()` now consults `MSFT_PhysicalDisk.MediaType` /
  `BusType` from the `root\Microsoft\Windows\Storage` namespace —
  Windows' authoritative classification — and only falls back to the
  model-name heuristic if that namespace is unavailable. Delete the
  cached `hardware_profile.json` once after upgrading to pick up the
  corrected drive type.
- Settings → Drivers: Realtek's own download portal returns 404 at every
  public path — switched the Realtek row to point at a Microsoft Update
  Catalog search filtered for Realtek, which actually surfaces working
  driver packages.  Razer link now points to `razer.com/synapse-3` (the
  prior `mysupport.razer.com/app/downloads` URL 404s).
- Settings → About → Build panel now actually lists Python / Platform / CPU /
  GPU / RAM rows again.  A local variable named `build` was being clobbered
  from a `tk.Frame` to a string mid-function, which silently broke the
  row-building loop.
- Settings → Drivers → Refresh no longer leaves an orphaned
  "Loading drivers..." label above the populated list.  The refresh path
  now destroys any prior loader widget up front and uses a generation
  counter so in-flight queries from a previous click can't touch the UI.
- Settings → Drivers: Realtek and Razer download links now point to working
  vendor portals (`realtek.com/downloads/` and
  `mysupport.razer.com/app/downloads`) — the previous URLs had rotted.
- Settings → Service → System panel no longer reports AWCC "Not found"
  when Alienware Command Center is installed under the modern
  `C:\Program Files\Alienware\Alienware Command Center\` path. The check
  now also scans the Alienware folder for any AWCC-bearing subfolder and
  re-probes on hardware-cache load so existing profiles self-heal without
  a first-run re-scan.
- Settings → Drivers: every row now has a working download link. Added a
  broader vendor map (Realtek Audio / LAN, Intel ARC, Killer, Qualcomm
  Atheros, Broadcom, MediaTek, etc.) and a Windows Update Catalog
  fallback so nothing shows as "no link available".
- Settings → About: email address is plain text (Send Feedback button
  already handles delivery); removed the duplicate "Open GitHub" button
  (the URL above is clickable).
- Settings → Display → Overlay opacity slider now actually changes the
  sensor bar's transparency. The bar reads `display.overlay_opacity` on
  launch and re-applies it live whenever the slider moves, instead of
  staying hardcoded at 94%.
- Sensor bar stays populated during all-core stress tests (OCCT, Prime95,
  etc.) instead of flipping CPU / GPU / NVMe cells to "---". The LHM
  bridge process now launches at above-normal priority so the Windows
  scheduler keeps giving it CPU time under saturation, the last known
  temps are served for up to 30 s (dimmed) while the bridge misses polls,
  and the restart backoff allows three immediate retries before doubling
  (cap lowered 60 s → 30 s) so a transient miss no longer locks readings
  out for a full minute.
- Post-update "What's New" dialog no longer silently skips itself the first
  time you launch after applying an update.
- YubiKey dev-unlock landing after Settings prewarmed its tabs no longer
  leaves CPU / GPU / RAM / AI panels stuck on "Sign in" lock cards — the
  gated tabs now rebuild automatically once the in-memory session flips.
  Inline "Sign In" buttons on gated panels also trigger the rebuild on
  return from the login subprocess.
- Settings → Account → Licenses & Add-ons now actually disappears after
  the user owns the base license + Pro add-on. Previously the section
  was rendered once at tab-build time, so if the Account tab was opened
  before YubiKey dev-unlock or backend license refresh resolved, the
  purchase rows would render and never re-evaluate — leaving "buy this!"
  cards on screen even after the status row above flipped to "Licensed:
  Base License · Pro Add-on". The section now lives in a stable container
  that gets rebuilt every time auth state changes (initial render, sign-
  in/out, license refresh, purchase completion, YubiKey unlock poll).

[1.0.0]: https://github.com/mourninggrace/AlienCore/releases/tag/v1.0.0
