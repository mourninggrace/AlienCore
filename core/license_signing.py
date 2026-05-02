"""
AlienCore - license_signing.py
Ed25519 verification of license payloads returned by the backend.

Why this exists:  prior to v1.0 the client's HMAC-signed session.json
protected against post-issuance tampering, but the HMAC key was derived
solely from the (deterministic, public) machine fingerprint.  An attacker
who could intercept /auth/check responses (local proxy, hostile DNS,
captive-portal MITM) could rewrite has_pro=True; the client would then
re-sign the file under its own HMAC key and the change persisted forever.

Fix: the server now signs each license payload with an Ed25519 private
key it alone holds.  The client refuses to trust has_base / has_pro /
trial_started_at unless `verify(payload, sig)` returns True.  An attacker
without the private key can no longer manufacture a passing payload.

Canonical signing string:  see _canonical_bytes() — must match the
backend's `_sign_license` exactly.
"""

import base64
import logging
import time

from core.license_pubkey import PUBLIC_KEY_B64

logger = logging.getLogger("aliencore.license_signing")

# Maximum age of a license signature.  The server includes `signed_at` in
# the canonical payload; we reject anything older than this so an attacker
# who captured a valid signed payload yesterday can't replay it after the
# user's license was revoked.
_MAX_SIG_AGE_SEC = 14 * 86400


def _canonical_bytes(payload: dict) -> bytes:
    """Build the exact byte sequence the backend signed.  Field order and
    serialization MUST match `backend/server.py::_sign_license` byte-for-byte
    or every signature will fail to verify."""
    parts = [
        payload.get("email", ""),
        "1" if payload.get("has_base") else "0",
        "1" if payload.get("has_pro") else "0",
        f"{int(payload.get('trial_started_at') or 0)}",
        f"{int(payload.get('expires_at') or 0)}",
        f"{int(payload.get('issued_at') or 0)}",
        f"{int(payload.get('signed_at') or 0)}",
        payload.get("fingerprint", ""),
    ]
    return "|".join(parts).encode("utf-8")


def _load_public_key():
    """Decode the embedded base64 public key.  Returns (key, error_msg).
    A blank or malformed key returns (None, msg) so the caller can
    fail-closed without crashing."""
    if not PUBLIC_KEY_B64 or all(c in ("A", "=") for c in PUBLIC_KEY_B64):
        return None, "License public key is not configured (placeholder)"
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        raw = base64.b64decode(PUBLIC_KEY_B64, validate=True)
        if len(raw) != 32:
            return None, f"Public key wrong length: {len(raw)}"
        return Ed25519PublicKey.from_public_bytes(raw), None
    except ImportError:
        return None, "cryptography package missing"
    except Exception as e:
        return None, f"Public key load failed: {e}"


_pub_cache = (None, None, False)   # (key, error, loaded)


def is_configured() -> bool:
    """True if the embedded public key is real (not the placeholder)."""
    global _pub_cache
    key, err, loaded = _pub_cache
    if not loaded:
        key, err = _load_public_key()
        _pub_cache = (key, err, True)
        if err:
            logger.warning("License signing: %s — Pro/Base gates fail closed.", err)
    return key is not None


def verify(payload: dict, sig_b64: str) -> bool:
    """Return True iff sig_b64 is a valid Ed25519 signature over the
    canonical bytes of payload.  Any failure (missing key, malformed sig,
    stale signed_at, signature mismatch) returns False — there is no
    permissive fallback."""
    if not is_configured():
        return False
    key, _err, _ = _pub_cache

    if not isinstance(sig_b64, str) or not sig_b64:
        return False
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except Exception:
        return False
    if len(sig) != 64:
        return False

    signed_at = payload.get("signed_at")
    try:
        signed_at_f = float(signed_at) if signed_at is not None else 0.0
    except (TypeError, ValueError):
        return False
    now = time.time()
    if signed_at_f <= 0:
        return False
    # Reject signatures from the far future (server clock drift) or older
    # than _MAX_SIG_AGE_SEC (replay window).
    if signed_at_f > now + 600:
        logger.warning("License signature signed_at is in the future — rejected")
        return False
    if now - signed_at_f > _MAX_SIG_AGE_SEC:
        logger.warning("License signature too old (%.0f s) — rejected", now - signed_at_f)
        return False

    canonical = _canonical_bytes(payload)
    try:
        key.verify(sig, canonical)
        return True
    except Exception:
        return False
