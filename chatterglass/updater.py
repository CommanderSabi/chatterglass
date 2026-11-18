# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Update checking against GitHub releases.

Only the *check* is implemented here; applying an update is deliberately kept
behind signature verification and happens on the next start, never mid-run.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Dict, Optional

# Placeholder until the repository exists; overridable in config.json.
DEFAULT_REPO = "CommanderSabi/chatterglass"
API = "https://api.github.com/repos/%s/releases/latest"
USER_AGENT = "Chatterglass-update-check"


def _version_tuple(text: str) -> tuple:
    cleaned = (text or "").strip().lstrip("vV")
    parts = []
    for chunk in cleaned.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def check(current_version: str, repo: Optional[str] = None,
          timeout: float = 8.0) -> Dict[str, object]:
    """Return {'available': bool, 'latest': str, 'url': str, 'error': str|None}."""
    repo = repo or DEFAULT_REPO
    request = urllib.request.Request(
        API % repo, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"available": False, "latest": "", "url": "", "error": "HTTP %s" % error.code}
    except Exception as error:  # noqa: BLE001
        return {"available": False, "latest": "", "url": "", "error": str(error)}

    tag = str(payload.get("tag_name") or "")
    url = ""
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.endswith(".zip"):
            url = str(asset.get("browser_download_url") or "")
            break
    return {
        "available": bool(tag) and is_newer(tag, current_version),
        "latest": tag.lstrip("vV"),
        "url": url,
        "error": None,
    }


# ---------------------------------------------------------------- download
def _get(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def download_and_verify(zip_url: str, expected_version: str) -> str:
    """Download a release zip and refuse it unless the signature checks out.

    Every failure raises RuntimeError with a reason the UI can show. Nothing is
    unpacked here; install_state.apply_update does that after this returns.
    """
    import os
    import tempfile

    from . import release_key

    if not zip_url.endswith(".zip"):
        raise RuntimeError("release has no .zip asset")
    directory = os.path.join(os.path.expanduser("~"), ".cache", "chatterglass")
    os.makedirs(directory, mode=0o700, exist_ok=True)

    name = os.path.basename(zip_url)
    zip_path = os.path.join(directory, name)
    sig_path = zip_path + ".sig"

    try:
        payload = _get(zip_url)
        signature = _get(zip_url + ".sig")
    except Exception as error:  # noqa: BLE001
        raise RuntimeError("download failed: %s" % error)

    with open(zip_path, "wb") as handle:
        handle.write(payload)
    with open(sig_path, "wb") as handle:
        handle.write(signature)

    if not release_key.verify_signature(zip_path, sig_path):
        raise RuntimeError("signature does not match the embedded key — refusing")

    # The checksum file is signed too, so check it as well when present.
    try:
        checksums = _get(zip_url.rsplit("/", 1)[0] + "/SHA256SUMS").decode("utf-8")
        checksums_path = os.path.join(directory, "SHA256SUMS")
        with open(checksums_path, "w", encoding="utf-8") as handle:
            handle.write(checksums)
        checksums_sig = _get(zip_url.rsplit("/", 1)[0] + "/SHA256SUMS.sig")
        with open(checksums_path + ".sig", "wb") as handle:
            handle.write(checksums_sig)
        if not release_key.verify_signature(checksums_path, checksums_path + ".sig"):
            raise RuntimeError("SHA256SUMS signature does not match — refusing")
        import hashlib
        expected = None
        for line in checksums.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == name:
                expected = parts[0]
        actual = hashlib.sha256(payload).hexdigest()
        if expected and expected != actual:
            raise RuntimeError("checksum mismatch — refusing")
    except RuntimeError:
        raise
    except Exception:
        # No checksum assets published: the signature above already covers it.
        pass

    return zip_path
