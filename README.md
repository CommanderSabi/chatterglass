# Chatterglass

Transparent chat overlays and a Discord voice panel that float above your game on
KDE Wayland. Click-through when you want them out of the way, clickable when you
want to scroll.

**Status: working and in use.** Built on Bazzite and tested on the Universal Blue family (Bazzite, Aurora,
Bluefin) and should work on any distro with the packages listed below.

## What it does

- **Discord voice panel** — who is in your current voice channel, their server
  nickname and avatar, a green ring on whoever is speaking, mute/deafen/video
  badges. Joins, leaves and mutes update instantly.
- **Chat overlays** — any web page: TikFinity's TikTok overlay, a Twitch widget,
  anything with a URL. Or use the built-in **Multi-chat** tab.
- **Multi-chat** — type your Twitch / YouTube / Kick usernames, pick options, get
  one combined chat panel. This uses Colin Horn's overlay (see Credits); the app
  builds its address and displays it.
- Each panel has its own position, size, zoom, background opacity and show/hide
  switch, and can be made completely click-through.
- **OBS page:** Settings → *OBS / browser source* serves a loopback-only page that
  reproduces your overlay layout, so a single OBS Browser Source captures only the
  overlays — no app window, no compositing. Off by default; closes with the app.
- **On screen switch:** turn the on-screen overlays off entirely and use them only
  inside OBS, keeping your layout, chat addresses and positions.

## Installing

1. Download **Chatterglass.zip** from the latest release and unpack it.
2. Run **`install.sh`** — double-click it, or in a terminal `./install.sh`.
   If your file manager opens it in an editor instead, right-click → **Run**.
3. In the window that appears, press **Install Chatterglass**.
4. Launch **Chatterglass** from your application menu.

It installs to `~/.local/share/chatterglass` plus a menu entry and icons. There is no
system-wide install and no service. The unpacked folder can be deleted afterwards.

**Uninstall:** Settings → **Uninstall Chatterglass** (it asks first), which removes the
program, the menu entry, the icons, your saved Discord token, and revokes the Discord
authorisation. The CLI equivalent is
`~/.local/share/chatterglass/chatterglass-run --uninstall`.

**Developers:** the raw program is `chatterglass.pyz` — one file, runnable with
`python3 chatterglass.pyz`, verifiable against `chatterglass.pyz.sig`.

## Discord: the one-time authorise

The panel reads the voice channel you are currently in, and only that. It cannot
read messages or act as you, and nothing is sent to any server of ours — there
isn't one.

The first time, press **Connect to Discord**: Discord's authorise page opens in
your browser, you press **Authorise**, and the token is stored in `config.json`
(mode 600). It renews itself from then on. Being invisible or silent makes no
difference; being *in* a channel is all it needs.

## Requirements

Already present on Universal Blue images (Bazzite, Aurora, Bluefin) and on
Silverblue/Kinoite; on other distros install them from your package manager:

- `python3-gobject` with GTK 3 — the window and control panel
- `webkit2gtk4.1` — renders the chat overlays
- `gtk-layer-shell` (`GtkLayerShell-0.1` typelib) — puts the overlays above games

Check with `./chatterglass-run --check`.

## Known limits

- **Exclusive fullscreen cannot be overlaid** by anything. Use borderless or
  windowed fullscreen.
- Discord's local RPC is undocumented; it can change without notice.
- A chat page that paints its own opaque background will cover the injected
  backdrop, so the background slider appears to do nothing for those pages.
- Access tokens expire; the stored refresh token is tried first, and if that
  fails the browser authorise is shown again.

## Verifying a release

Releases are signed with **Ed25519**. The signing key's fingerprint — SHA-256 of the
shipped `chatterglass/release-pubkey.pem` file, i.e. exactly what
`sha256sum` prints — is:

```
8a59d53b926bffd086613f4faf1d3d8cce079e50cc7c58e34a971ea3e4e8aa48
```

To check a download:

```bash
# the zip (what most people download)
openssl pkeyutl -verify -rawin -pubin -inkey release-pubkey.pem \
    -in Chatterglass.zip -sigfile Chatterglass.zip.sig

# and the checksums listed in the release
sha256sum -c SHA256SUMS
```

Both should say "Signature Verified Successfully" and `OK`. Developers who take the
raw program instead verify `chatterglass.pyz` against `chatterglass.pyz.sig` the
same way.

**If the key or fingerprint you see differs from the one above, do not install it.**
That is the signature of a compromised download, not a new key. The public key is
shipped at `chatterglass/release-pubkey.pem` and embedded in the app; an installed
copy verifies every update against the embedded key and refuses anything signed
otherwise. The private key never ships and is not in this repository.

## Credits

- **Colin Horn** — the multi-chat overlay is his project:
  <https://chatoverlay.colinhorn.co.uk> · <https://www.colinhorn.co.uk>
  Chatterglass only builds its URL and displays it. Not affiliated.
- **Overlayed** (<https://github.com/overlayeddev/overlayed>) and **Discover**
  (<https://github.com/trigg/Discover>) — prior art; the Discord IPC approach and
  the StreamKit/PKCE findings came from reading how they solve the same problem.
  No code was copied.

## Support

Chatterglass is free and open-source. If it helps you, a small donation
helps free up time to keep improving it.

- **Patreon:** https://www.patreon.com/cw/CommanderSabi
- **Cryptocurrency — USDC (Solana network only):**
  `2stgEZfERUMs7naTjikdR4CkDUnU1gsYKSHCX4VH5DQt`
  (a cryptocurrency payment — send the USDC token on the Solana network
  only; sending it on another network can lose the funds)

## Licence

MIT — Copyright (c) 2026 Commander Sabi. You may use, modify and redistribute
this, including commercially; keep the copyright notice and the licence text.

Third-party credits are in `NOTICE` (Colin Horn's overlay, Overlayed and
Discover as prior art, and the toolkit libraries).
