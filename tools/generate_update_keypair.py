"""
AlienCore - tools/generate_update_keypair.py

Generates the Ed25519 keypair used to sign auto-update release artifacts and
verify them on the client.  This is SEPARATE from the license keypair — do not
reuse the license key for update signing.  Run ONCE (already done during the
security-hardening pass; rotating invalidates old clients' ability to verify
new releases and requires a versioned cutover).

    python tools/generate_update_keypair.py

Outputs:
  · `update_private.pem`  — KEEP SECRET.  Used by tools/sign_release.py to sign
                            every release zip.  NEVER commit (git-ignored).
  · `update_public.b64`   — base64-encoded raw 32-byte public key.  Paste into
                            core/update_pubkey.py as UPDATE_PUBLIC_KEY_B64.

Threat model: see core/update_pubkey.py.  The updater runs as Administrator;
without this signature an attacker controlling the GitHub release response
(MITM or release-edit rights) achieves admin RCE.
"""

import argparse
import base64
import os
import sys

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    print("FATAL: cryptography package missing.  pip install cryptography", file=sys.stderr)
    sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=".",
                    help="Directory to write update_private.pem / update_public.b64")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing key files (default: refuse)")
    args = ap.parse_args()

    priv_path = os.path.join(args.out_dir, "update_private.pem")
    pub_path  = os.path.join(args.out_dir, "update_public.b64")

    if (os.path.exists(priv_path) or os.path.exists(pub_path)) and not args.force:
        print(f"FATAL: refusing to overwrite existing key files in {args.out_dir!r}.", file=sys.stderr)
        print("       Use --force if you really mean to rotate.", file=sys.stderr)
        sys.exit(1)

    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_raw).decode("ascii")

    with open(priv_path, "wb") as f:
        f.write(priv_pem)
    try:
        os.chmod(priv_path, 0o600)
    except Exception:
        pass
    with open(pub_path, "w") as f:
        f.write(pub_b64 + "\n")

    print("Update keypair generated.")
    print(f"  private (KEEP SECRET): {priv_path}")
    print(f"  public  (embed in client): {pub_path}")
    print()
    print("Next steps:")
    print(f"  1. Paste the contents of {pub_path} into core/update_pubkey.py")
    print(f"     as UPDATE_PUBLIC_KEY_B64 = \"...\".")
    print(f"  2. Keep {priv_path} secret and add it to .gitignore.")
    print(f"  3. Sign every release with: python tools/sign_release.py <zip> --key {priv_path}")


if __name__ == "__main__":
    main()
