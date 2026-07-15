"""
AlienCore - tools/sign_release.py

Produces the detached Ed25519 signature that the auto-updater requires before
it will apply a release.  Run this for EVERY release, against the exact zip
the client will download (GitHub's zipball for the tag).

    python tools/sign_release.py path/to/aliencore-1.0.1.zip --key update_private.pem

Output:
  · Prints a `sig:<base64>` line — paste this into the GitHub release body
    (alongside the existing `sha256:<hex>` line) so the client can parse it.
  · Also writes `<zip>.sig` (raw base64) next to the zip for convenience.

Threat model: the updater runs AS ADMINISTRATOR and overlays the downloaded
tree over the install dir.  The SHA-256 in the release body is parsed from the
same GitHub response that supplies the zip URL, so it is NOT a trust root — an
attacker who controls that response supplies both.  The client therefore
requires an Ed25519 signature over the raw zip bytes, verified against the
public key embedded in core/update_pubkey.py.  Only the holder of
update_private.pem (this signer) can produce a passing signature.

IMPORTANT:
  · update_private.pem MUST be kept secret and is git-ignored.  Anyone with it
    can ship admin-level code to every user.  Never commit it, never put it in
    the installer, never paste it anywhere.
  · The zip you sign MUST be byte-identical to what users download.  For GitHub
    source zipballs, download the actual zipball_url artifact and sign THAT.

To verify your signature locally before publishing:
    python tools/sign_release.py path/to.zip --key update_private.pem --verify
"""

import argparse
import base64
import os
import sys

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey,
    )
except ImportError:
    print("FATAL: cryptography package missing.  pip install cryptography", file=sys.stderr)
    sys.exit(2)


def _load_private_key(path: str) -> Ed25519PrivateKey:
    with open(path, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        print(f"FATAL: {path} is not an Ed25519 private key.", file=sys.stderr)
        sys.exit(1)
    return key


def main():
    ap = argparse.ArgumentParser(description="Sign an AlienCore release zip with the update private key.")
    ap.add_argument("zip", help="Path to the release zip (the exact bytes clients download).")
    ap.add_argument("--key", default="update_private.pem",
                    help="Path to update_private.pem (default: ./update_private.pem)")
    ap.add_argument("--verify", action="store_true",
                    help="Also verify the produced signature against the embedded public key.")
    args = ap.parse_args()

    if not os.path.exists(args.zip):
        print(f"FATAL: zip not found: {args.zip}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.key):
        print(f"FATAL: private key not found: {args.key}", file=sys.stderr)
        sys.exit(1)

    with open(args.zip, "rb") as f:
        data = f.read()

    priv = _load_private_key(args.key)
    sig = priv.sign(data)
    sig_b64 = base64.b64encode(sig).decode("ascii")

    sig_path = args.zip + ".sig"
    with open(sig_path, "w", encoding="utf-8") as f:
        f.write(sig_b64 + "\n")

    print("Release signed.")
    print(f"  zip       : {args.zip} ({len(data)} bytes)")
    print(f"  sig file  : {sig_path}")
    print()
    print("Paste this line into the GitHub release body (with the sha256 line):")
    print()
    print(f"sig:{sig_b64}")
    print()

    if args.verify:
        # Verify against the public key embedded in the client.
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, repo_root)
        try:
            from core.update_pubkey import UPDATE_PUBLIC_KEY_B64
            raw = base64.b64decode(UPDATE_PUBLIC_KEY_B64, validate=True)
            pub = Ed25519PublicKey.from_public_bytes(raw)
            pub.verify(sig, data)
            print("VERIFY OK: signature validates against embedded core/update_pubkey.py.")
        except Exception as e:
            print(f"VERIFY FAILED: {e}", file=sys.stderr)
            print("  The private key does NOT match the embedded public key — clients "
                  "would reject this release.", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
