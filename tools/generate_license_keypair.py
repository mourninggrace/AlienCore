"""
AlienCore - tools/generate_license_keypair.py

Generates an Ed25519 keypair used to sign license payloads on the backend
and verify them on the client.  Run ONCE during deploy:

    python tools/generate_license_keypair.py

Outputs:
  · `license_private.pem`       — KEEP SECRET.  Set as AC_LICENSE_PRIVATE_KEY
                                  on the backend (or paste-load the PEM bytes
                                  via your env-var manager).  NEVER commit.
  · `license_public.b64`        — base64-encoded raw 32-byte public key.
                                  Paste this into core/license_pubkey.py
                                  as PUBLIC_KEY_B64 before the v1.0 release.

Threat model: an attacker who can MITM /auth/check (or compromise the
update channel) cannot grant themselves Pro by flipping has_pro=True in
the response, because the client refuses to trust any license payload
without a valid Ed25519 signature over the canonical payload bytes.
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
                    help="Directory to write license_private.pem / license_public.b64")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing key files (default: refuse)")
    args = ap.parse_args()

    priv_path = os.path.join(args.out_dir, "license_private.pem")
    pub_path  = os.path.join(args.out_dir, "license_public.b64")

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
    os.chmod(priv_path, 0o600)
    with open(pub_path, "w") as f:
        f.write(pub_b64 + "\n")

    print("Keypair generated.")
    print(f"  private (KEEP SECRET): {priv_path}")
    print(f"  public  (embed in client): {pub_path}")
    print()
    print("Next steps:")
    print(f"  1. Set AC_LICENSE_PRIVATE_KEY on the backend to the contents of {priv_path}")
    print(f"     (OR set AC_LICENSE_PRIVATE_KEY_PATH={priv_path} on the server only)")
    print(f"  2. Paste the contents of {pub_path} into core/license_pubkey.py")
    print(f"     as PUBLIC_KEY_B64 = \"...\".")
    print(f"  3. Add license_private.pem to .gitignore — never commit it.")


if __name__ == "__main__":
    main()
