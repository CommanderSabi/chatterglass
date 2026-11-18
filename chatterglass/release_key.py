# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Release-signing public key — the trust anchor for updates.

The PRIVATE key never ships and never enters the repository: it lives outside
this folder (default ``~/.config/chatterglass-signing/``) on the author's machine
only. This public half is embedded here as a constant rather than shipped as a
separate .pem file, so it cannot be swapped out next to the code by anyone who
can write to the install directory.

Same scheme as Horidoro AV: Ed25519 via the system ``openssl`` CLI, detached
``.sig`` beside each released artifact.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

RELEASE_PUBKEY_PEM = """\
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAM91FgW0pR+oaojHNGpUPQ33NRtIn40ajmA2O5vwUW3o=
-----END PUBLIC KEY-----
"""


def verify_signature(file_path: str, signature_path: str) -> bool:
    """True only when the detached signature matches the embedded key."""
    if not (os.path.exists(file_path) and os.path.exists(signature_path)):
        return False
    handle = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
    try:
        handle.write(RELEASE_PUBKEY_PEM)
        handle.close()
        os.chmod(handle.name, 0o600)
        result = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin",
             "-inkey", handle.name, "-in", file_path,
             "-sigfile", signature_path],
            capture_output=True, text=True,
        )
        return result.returncode == 0 and "Verified" in (result.stdout + result.stderr)
    except OSError:
        return False
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
