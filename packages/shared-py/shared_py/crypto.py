"""Token encryption helpers using Fernet symmetric encryption.

The encryption key is read from the ``TOKEN_ENCRYPTION_KEY`` environment
variable. If the variable is absent or empty the module raises ``RuntimeError``
at import time — there is no silent fallback to a weak key.

Generate a key with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Then set ``TOKEN_ENCRYPTION_KEY=<output>`` in your ``.env``.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet

_RAW_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip()
if not _RAW_KEY:
    raise RuntimeError(
        "TOKEN_ENCRYPTION_KEY environment variable is not set. "
        "Generate one with: "
        "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )

_fernet = Fernet(_RAW_KEY.encode())


def encrypt_token(plaintext: str) -> str:
    """Encrypt *plaintext* and return a URL-safe base64 ciphertext string."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a ciphertext produced by :func:`encrypt_token`."""
    return _fernet.decrypt(ciphertext.encode()).decode()


def mask_token(token: str) -> str:
    """Return a safe log-friendly representation: first 3 + last 3 chars.

    Examples::

        mask_token("EAAAABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        # -> "EAA...xyz"
    """
    if len(token) <= 8:
        return "***"
    return f"{token[:3]}...{token[-3:]}"
