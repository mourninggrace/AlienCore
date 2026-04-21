# Changelog

All notable changes to AlienCore are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Default sensor bar opacity lowered from 85% to 75%.
- Settings → CPU tab now shows both base and boost frequency when they
  differ.  WMI and psutil both report the CPU's base clock (e.g. 2,200 MHz
  for the i9-14900HX) as "max", which was misleading on any machine
  capable of Turbo Boost.  A built-in lookup table covers Intel 12/13/14th
  gen + Core Ultra and AMD Ryzen 5000/7000/9000 — the i9-14900HX now shows
  Base 2,200 MHz / Max boost 5,800 MHz.  CPUs not in the table fall back
  to the highest per-core clock LHM has ever observed on this machine.

### Fixed

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
