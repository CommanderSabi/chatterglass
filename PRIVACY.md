# Privacy

Chatterglass runs entirely on your computer. There is no account to create, no
telemetry, and no server of mine that anything is sent to.

## What is stored, and where

| Data | Where | Permissions |
|---|---|---|
| Your settings and overlay layout | `~/.local/share/chatterglass/config.json` | `0600` (only you) |
| Your Discord access + refresh token | the same `config.json` | `0600` |
| Nothing else | — | — |

There is no log file, no database, and no cache outside what GTK and WebKit keep
for any application. WebKit runs with an **ephemeral** context, so chat pages
leave no cookies, cache or local storage behind.

## What leaves your computer

1. **Discord.** The app talks to Discord's local socket (`discord-ipc-0`) on your
   machine to read your current voice channel, and to `discord.com` during the
   one-time authorisation and token refresh. It sends nothing else to Discord.
2. **The chat overlay pages you add.** Each overlay is an ordinary web page, run
   inside its own WebKit view, talking to whatever service it belongs to (for
   example Colin Horn's multi-chat overlay, TikFinity, or Twitch). Those services
   see what any browser would see: your IP address and their own cookies, which
   are not persisted here.
3. **Update checks**, if you leave them on: a request to GitHub's public releases
   API for the project. No identifiers are sent.

Nothing is sent to the author of this app. There is no analytics, no crash
reporting, and no usage data.

## What it can see

- The Discord voice channel you are currently in, and who is in it: display
  names, avatars, and speaking/mute/deaf state. It cannot read messages, direct
  messages, your friends list, or your account.
- Nothing from other applications, and nothing from your files.

## Deleting everything

- In the app: **Settings → Uninstall**. This removes the program, the menu entry,
  the icons, your saved Discord token, and revokes the authorisation.
- By hand: quit the app, delete `~/.local/share/chatterglass`, and remove
  `~/.local/share/applications/chatterglass.desktop` plus
  `~/.local/share/icons/hicolor/*/apps/chatterglass.png`.
- To withdraw Discord's authorisation specifically: Discord → **User Settings →
  Authorised Apps → Chatterglass → Deauthorise**.

## Bug reports

**Report a bug** shows you exactly what it will contain before anything is
shared, and nothing is shared until you press Copy, Save or Open issue. It
includes the app version, your distribution and kernel, session type, library
versions, install state, Discord connection status, and the size/visibility of
your overlays. It excludes chat URLs, channel names, your Discord username and
token, message text, and your files. Report text is also cleaned line by line so
it cannot carry hidden instructions to whatever reads it.

## Third parties

Chat overlays are provided by other people; their own privacy policies apply to
them. The multi-chat overlay is Colin Horn's project
(<https://chatoverlay.colinhorn.co.uk>). Discord is a trademark of Discord Inc.,
and this app is not affiliated with or endorsed by them.
