#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Release tooling for Chatterglass: key generation, packaging, signing, verify.

Deliberately dependency-free: Ed25519 comes from the system `openssl` CLI, so
nothing has to be installed to make or check a release.

    ./release.py keygen                 # once, on this machine only
    ./release.py build                  # -> dist/chatterglass-<version>.zip
    ./release.py sign                   # detached Ed25519 signatures
    ./release.py verify                 # what a user (or an updater) runs

The private key lives OUTSIDE this folder by default
(~/.config/chatterglass-signing/) so it can never end up in a zip or a commit.
Only the public half is kept in the project, at chatterglass/release-pubkey.pem.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

PROJECT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(PROJECT, "dist")
PUBKEY = os.path.join(PROJECT, "chatterglass", "release-pubkey.pem")
DEFAULT_KEYDIR = os.path.join(os.path.expanduser("~"), ".config", "chatterglass-signing")
PRIVATE_KEY = "chatterglass-ed25519.key"

# Never put the version in the released filename: the website links to
# releases/latest/download/<name>, which must resolve for every future release.
ARCHIVE_NAME = "Chatterglass.zip"

# Same model as Horidoro AV: the key lives as a passphrase-protected GPG archive,
# is decrypted into a 0700 temp dir only for the signing step, and is wiped after.
# The directory holding the archive is private/ and is git-ignored.
KEY_ARCHIVE_DEFAULT = os.path.join(PROJECT, "private", "release", "keys",
                                   "chatterglass-release-key.pem.gpg")

#include order matters only for determinism of the listing, not the content
INCLUDE_FILES = ["README-FIRST.txt", "install.sh", "chatterglass.pyz",
                 "README.md", "LICENSE", "NOTICE", "PRIVACY.md", "SECURITY.md",
                 "DISCLAIMER.md",
                 "config.example.json"]
INCLUDE_DIRS: list = []
EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_FILES = {"config.json"}


def version() -> str:
    namespace: dict = {}
    path = os.path.join(PROJECT, "chatterglass", "__init__.py")
    with open(path, "r", encoding="utf-8") as handle:
        exec(compile(handle.read(), path, "exec"), namespace)
    return namespace.get("__version__", "0.0.0")


def _openssl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["openssl", *args], capture_output=True, text=True)


# ---------------------------------------------------------------- keygen
def cmd_keygen(args) -> int:
    keydir = os.path.abspath(args.key_dir)
    os.makedirs(keydir, mode=0o700, exist_ok=True)
    os.chmod(keydir, 0o700)
    private = os.path.join(keydir, PRIVATE_KEY)

    if os.path.exists(private) and not args.force:
        print("refusing to overwrite existing key: %s" % private, file=sys.stderr)
        print("pass --force only if you really mean to invalidate every released "
              "signature", file=sys.stderr)
        return 1

    result = _openssl("genpkey", "-algorithm", "ed25519", "-out", private)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1
    os.chmod(private, 0o600)

    result = _openssl("pkey", "-in", private, "-pubout", "-out", PUBKEY)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1

    print("private key : %s (mode 600 — back this up, never share it)" % private)
    print("public key  : %s (safe to publish, shipped in every copy)" % PUBKEY)
    return 0


# ---------------------------------------------------------------- build
def _collect() -> list[tuple[str, str]]:
    """[(absolute source, archive name)] for everything that ships."""
    entries: list[tuple[str, str]] = []
    for name in INCLUDE_FILES:
        path = os.path.join(PROJECT, name)
        if os.path.exists(path):
            entries.append((path, name))
    for directory in INCLUDE_DIRS:
        root = os.path.join(PROJECT, directory)
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
            for filename in sorted(filenames):
                if filename in EXCLUDE_FILES or filename.endswith(".pyc"):
                    continue
                path = os.path.join(current, filename)
                entries.append((path, os.path.relpath(path, PROJECT)))
    return sorted(entries, key=lambda item: item[1])


def build_pyz() -> str:
    """Build the single-file program from the package plus embedded assets."""
    subprocess.run([sys.executable, os.path.join(PROJECT, "make_assets.py")],
                   check=True, capture_output=True)
    staging = tempfile.mkdtemp(prefix="chatterglass-pyz-")
    shutil.copytree(os.path.join(PROJECT, "chatterglass"),
                    os.path.join(staging, "chatterglass"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(staging, "__main__.py"), "w", encoding="utf-8") as handle:
        handle.write(
            '#!/usr/bin/env python3\n'
            '"""Chatterglass, single file. Run: python3 chatterglass.pyz"""\n'
            "import sys\n"
            "from chatterglass.__main__ import main\n\n"
            'if __name__ == "__main__":\n'
            "    sys.exit(main())\n")
    target = os.path.join(PROJECT, "chatterglass.pyz")
    if os.path.exists(target):
        os.remove(target)
    import zipapp
    zipapp.create_archive(staging, target=target,
                          interpreter="/usr/bin/env python3", compressed=True)
    shutil.rmtree(staging, ignore_errors=True)
    os.chmod(target, 0o755)
    print("built %s (%.0f KiB)" % (os.path.basename(target),
                                   os.path.getsize(target) / 1024))
    return target


def cmd_build(args) -> int:
    build_pyz()
    os.makedirs(DIST, exist_ok=True)
    archive = os.path.join(DIST, ARCHIVE_NAME)
    entries = _collect()

    # Refuse to build anything that still carries a token or a private key.
    # The *public* key must ship, so it is exempt by name.
    forbidden = [name for _src, name in entries
                 if name.endswith("config.json")
                 or name.endswith(".key")
                 or (name.endswith(".pem")
                     and os.path.basename(name) != "release-pubkey.pem")]
    if forbidden:
        print("refusing to package sensitive files: %s" % ", ".join(forbidden),
              file=sys.stderr)
        return 1

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for source, name in entries:
            executable = name.endswith((".sh", ".desktop")) or name == "chatterglass-run"
            info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
            info.external_attr = (0o100000 | (0o755 if executable else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(source, "rb") as handle:
                bundle.writestr(info, handle.read())
            print("  + %s" % name)

    checksums = os.path.join(DIST, "SHA256SUMS")
    lines = []
    for path in [archive] + [p for p in release_files() if p != archive]:
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        lines.append("%s  %s" % (digest, os.path.basename(path)))
    with open(checksums, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    print("\n%s\nsha256 %s\nfiles  %d" % (archive, digest, len(entries)))
    return 0


# ---------------------------------------------------------------- sign / verify
def release_files() -> list:
    """The zip for users, the raw .pyz for developers, and the checksums."""
    archive = os.path.join(DIST, ARCHIVE_NAME)
    program = os.path.join(PROJECT, "chatterglass.pyz")
    return [path for path in (archive, program) if os.path.exists(path)]


def _targets() -> list:
    checksums = os.path.join(DIST, "SHA256SUMS")
    return [checksums] + release_files() if os.path.exists(checksums) else release_files()


def _key_for_signing(args) -> tuple:
    """Return (path_to_pem, tempdir_or_None). Caller wipes tempdir."""
    archive = (getattr(args, "key_archive", None)
               or os.environ.get("CHATTERGLASS_KEY_ARCHIVE")
               or (KEY_ARCHIVE_DEFAULT if os.path.exists(KEY_ARCHIVE_DEFAULT) else None))
    if archive:
        archive = os.path.abspath(archive)
        if not os.path.exists(archive):
            raise SystemExit("no such key archive: %s" % archive)
        work = tempfile.mkdtemp(prefix="chatterglass-key-")
        os.chmod(work, 0o700)
        pem = os.path.join(work, "release-key.pem")
        print("Decrypting key archive (passphrase prompt follows)…")
        with open(pem, "wb") as handle:
            result = subprocess.run(["gpg", "--quiet", "--decrypt", archive],
                                    stdout=handle)
        if result.returncode != 0 or not os.path.getsize(pem):
            shutil.rmtree(work, ignore_errors=True)
            raise SystemExit("could not decrypt the key archive")
        os.chmod(pem, 0o600)
        return pem, work

    private = os.path.join(os.path.abspath(args.key_dir), PRIVATE_KEY)
    if not os.path.exists(private):
        raise SystemExit(
            "no key: pass --key-archive <file.gpg> (recommended) or run ./release.py keygen")
    return private, None


def cmd_sign(args) -> int:
    targets = _targets()
    if not targets:
        print("nothing to sign — run ./release.py build first", file=sys.stderr)
        return 1
    pem, work = _key_for_signing(args)
    try:
        for target in targets:
            result = _openssl("pkeyutl", "-sign", "-rawin", "-inkey", pem,
                              "-in", target, "-out", target + ".sig")
            if result.returncode != 0:
                print(result.stderr, file=sys.stderr)
                return 1
            print("signed %s" % os.path.basename(target))
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)
    return 0


def cmd_verify(args) -> int:
    if not os.path.exists(PUBKEY):
        print("no public key at %s" % PUBKEY, file=sys.stderr)
        return 1
    failures = 0
    for target in _targets():
        signature = target + ".sig"
        if not os.path.exists(signature):
            print("MISSING signature: %s" % os.path.basename(signature))
            failures += 1
            continue
        result = _openssl("pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", PUBKEY,
                          "-in", target, "-sigfile", signature)
        ok = result.returncode == 0 and "Verified" in (result.stdout + result.stderr)
        print("%s %s" % ("OK  " if ok else "FAIL", os.path.basename(target)))
        failures += 0 if ok else 1

    checksums = os.path.join(DIST, "SHA256SUMS")
    if os.path.exists(checksums):
        for line in open(checksums, encoding="utf-8"):
            parts = line.split()
            if len(parts) != 2:
                continue
            digest, name = parts
            candidates = [os.path.join(DIST, name), os.path.join(PROJECT, name)]
            path = next((c for c in candidates if os.path.exists(c)), None)
            if path is None:
                continue
            actual = hashlib.sha256(open(path, "rb").read()).hexdigest()
            match = digest == actual
            print("%s checksum %s" % ("OK  " if match else "FAIL", name))
            failures += 0 if match else 1
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key-dir", default=DEFAULT_KEYDIR,
                        help="where the private key lives (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="create an Ed25519 keypair")
    keygen.add_argument("--force", action="store_true")
    keygen.set_defaults(func=cmd_keygen)

    for name, function, help_text in (
        ("build", cmd_build, "build dist/chatterglass-<version>.zip"),
        ("sign", cmd_sign, "sign the archive and checksums"),
        ("verify", cmd_verify, "verify signatures and checksum"),
    ):
        command = sub.add_parser(name, help=help_text)
        if name == "sign":
            command.add_argument("--key-archive", default=None,
                                 help="GPG-encrypted key archive "
                                      "(default $CHATTERGLASS_KEY_ARCHIVE)")
        command.set_defaults(func=function)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
