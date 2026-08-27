"""Encrypt small per-user secrets at rest.

Only one thing needs this today: each person's Weights & Biases API key. That
key is a live credential to somebody's own W&B account, so storing it as plain
text in the database would make a database dump — or a stray query in a log —
enough to post runs, read private projects and burn someone else's quota.

Hashing is not an option the way it is for passwords: the key has to be handed
back to the training process verbatim, so it must be recoverable. Hence
symmetric encryption, with the key derived from ``JWT_SECRET`` so there is no
second secret to deploy and rotate.

That derivation has a consequence worth stating plainly: **rotating
``JWT_SECRET`` makes every stored key undecryptable.** Nothing is corrupted —
:func:`decrypt` returns ``None`` and the person is asked to paste their key
again — but they will have to. `docs/` should say so wherever rotation is
described.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from src.config import get_settings


def _fernet() -> Fernet:
    """Derive the encryption key from ``JWT_SECRET``.

    SHA-256 gives the 32 bytes Fernet wants from a secret of any length. The
    domain separator keeps this key distinct from the signing use of the same
    secret, so a weakness in one cannot be replayed against the other.
    """

    secret = get_settings().jwt_secret.encode("utf-8")
    digest = hashlib.sha256(b"telecollect-user-secret-v1:" + secret).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    """Return `value` as an opaque token safe to store."""

    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str | None:
    """Return the original value, or ``None`` if it cannot be read.

    Returns ``None`` rather than raising: a key encrypted under a previous
    ``JWT_SECRET`` is an expected state after rotation, and the caller's job is
    to ask for the key again, not to fail the request.
    """

    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return None


def preview(value: str) -> str:
    """A recognisable, non-reusable fragment: ``…``-prefixed last four chars.

    Shown so a person can tell which key is stored without the interface ever
    returning enough of it to use.
    """

    tail = value[-4:] if len(value) >= 4 else ""
    return f"…{tail}" if tail else "…"
