Chatterglass — READ ME FIRST
============================

INSTALL
  1. Unpack this zip somewhere you can find it again.
  2. Run  install.sh
       Double-click it. If it opens in a text editor instead, right-click the
       file and choose "Run", or open a terminal here and type:  ./install.sh
  3. In the window that appears, press "Install Chatterglass".
  4. Launch "Chatterglass" from your application menu.

  You can delete the unpacked folder afterwards; the installed copy is what runs.

WHAT IT NEEDS
  Linux with KDE or another Wayland compositor, and these packages:
    python3-gobject   gtk3   webkit2gtk4.1   gtk-layer-shell
  On Bazzite / Fedora Atomic they are already present. If something is missing,
  the installer window tells you exactly what.

FIRST RUN
  The Discord panel asks you to connect once: a browser page opens, you press
  Authorise, and it is remembered. It only ever reads the voice channel you are
  currently in. Chat overlays are just URLs you paste in.

REMOVE IT AGAIN
  Settings -> Uninstall. It removes the app, the menu entry, the icons and your
  saved Discord token. Nothing is left behind.

CHECK THE DOWNLOAD (optional)
  The download is signed. The signing key's fingerprint is:
    8a59d53b926bffd086613f4faf1d3d8cce079e50cc7c58e34a971ea3e4e8aa48
  See README.md for the two commands that check the zip and its checksums.

  If a check fails, or the fingerprint above does not match what you find
  elsewhere, do not run it.
