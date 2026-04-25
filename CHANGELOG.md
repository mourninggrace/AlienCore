# Changelog

All notable changes to AlienCore are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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

## [1.0.0] — 2026-04-21

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

### Security

- All auth traffic flows over HTTPS to `aliencoreapp.duckdns.org`.
- Session tokens are stored locally under `%APPDATA%\AlienCore\session.json`
  with atomic replace writes; a 72-hour offline grace period lets licensed
  users work without network access.
- Dev YubiKey serials are burned into the key's chip by Yubico and cannot
  be spoofed from source.

[1.0.0]: https://github.com/mourninggrace/AlienCore/releases/tag/v1.0.0
