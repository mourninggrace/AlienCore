"""
AlienCore - update_pubkey.py
Embedded Ed25519 public key used to verify the cryptographic signature over
auto-update release artifacts (the GitHub release zipball) BEFORE the updater
extracts or executes any of its contents.

This file is checked into source control on purpose — it is the public half
of an Ed25519 keypair generated separately from the license keypair.  The
private half (update_private.pem) lives ONLY on the release owner's machine
and is used by tools/sign_release.py to sign each release zip.  It must never
be committed, shipped in the installer, or placed in any client-side artifact.

Threat model: the updater runs AS ADMINISTRATOR (the app auto-elevates).  It
fetches a GitHub release, downloads zipball_url, and overlays the extracted
tree over the install dir.  Previously the only integrity check was a SHA-256
parsed out of the SAME GitHub API response that supplied the zip URL — so
anyone who controls that response (compromised / MITM'd api.github.com, or a
party with release-edit rights) could supply both a malicious zip and a
matching hash for an admin-level RCE.  There was NO cryptographic signature
over the payload.

Fix: every release zip must now carry a detached Ed25519 signature (a
`sig:<base64>` line in the release body) produced by the holder of
update_private.pem.  The client refuses to apply any update whose signature
is missing or does not verify against UPDATE_PUBLIC_KEY_B64 below.  An attacker
who controls the GitHub response but not the private key can no longer forge a
passing payload.

To (re)generate (rare — rotating invalidates the ability of old clients to
verify new releases, so it requires a versioned cutover):
  1. python tools/generate_update_keypair.py  (or reuse generate_license_keypair
     pattern) — but the canonical generator for this project lives inline in
     the deploy notes; the private key currently in use was generated during
     the security-hardening pass and saved to update_private.pem.
  2. Replace UPDATE_PUBLIC_KEY_B64 below with the new base64 public key.
  3. Ship a client update; re-sign all future releases with the new private key.
"""

# Base64-encoded raw 32-byte Ed25519 public key for UPDATE artifact signing.
# The matching private key lives ONLY in update_private.pem on the release
# owner's machine — it never appears in source control, the .exe installer,
# or any client-side artifact.  Sign releases with tools/sign_release.py.
UPDATE_PUBLIC_KEY_B64 = "8Huwt7QOAq2JtDupuHfcCFWf7HSi4R31cIbChVkL+Ng="
