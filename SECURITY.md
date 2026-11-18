# Security

## Reporting a problem

Found a bug, a crash, or something that behaves incorrectly? Open an issue:
<https://github.com/CommanderSabi/chatterglass/issues>

Please include:

- the version you are running (**Settings → About** shows it)
- what you did, what you expected, and what happened instead
- what your session is (KDE/Wayland or something else)

The app can build that report for you: **About → Report a bug…** shows exactly what
it will contain before anything is shared, and never includes your chat addresses,
channel names, username or Discord token.

## Security-sensitive issues

Please **do not** open a public issue for something exploitable. Use GitHub's
[private vulnerability reporting](https://github.com/CommanderSabi/chatterglass/security/advisories/new)
on this repository instead.

Useful details if you have them: what an attacker gains, what they need first
(local access? a hostile overlay URL? a modified release file?), and the exact
version.

## Verifying a release

Releases are signed with Ed25519. The signing key's fingerprint — SHA-256 of the
shipped `chatterglass/release-pubkey.pem` file, i.e. exactly what `sha256sum`
prints — is:

```
8a59d53b926bffd086613f4faf1d3d8cce079e50cc7c58e34a971ea3e4e8aa48
```

Check a download before running it:

```bash
openssl pkeyutl -verify -rawin -pubin -inkey release-pubkey.pem \
    -in Chatterglass.zip -sigfile Chatterglass.zip.sig
sha256sum -c SHA256SUMS
```

If the key or fingerprint differs from the one above, **do not install it** — that
is a compromised download, not a new key. The public key is embedded in the app, so
an installed copy refuses any update that is not signed with it.

## What the app can and cannot see

It reads the Discord voice channel you are currently in (display names, avatars,
speaking/mute/deaf state) over Discord's local socket. It cannot read messages,
direct messages, your friends list, or your account. Chat overlays are ordinary web
pages you chose to add, running in a sandboxed webview with no bridge into the app.
Details: [PRIVACY.md](PRIVACY.md).
