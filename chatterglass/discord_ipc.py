# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Discord local IPC client.

Discord exposes a Unix socket per user (``discord-ipc-0``). Frames are
``[int32 LE opcode][int32 LE length][UTF-8 JSON]``. This module is the only part
of Chatterglass that talks to anything undocumented, so it is deliberately
defensive: every failure degrades to "disconnected" and it retries forever.

Opcodes: 0 handshake, 1 frame, 2 close, 3 ping, 4 pong.
"""

from __future__ import annotations

import glob
import json
import os
import select
import socket
import struct
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from . import auth
from .auth import AuthError

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4

# A local peer declares its own frame length; refuse absurd ones instead of
# buffering whatever it claims (see private/audit/run-1).
MAX_FRAME_BYTES = 1 << 20  # 1 MiB

# Discord's local socket refuses voice commands until the app authenticates.
# See auth.py for why that is PKCE and not the RPC AUTHORIZE command.
SCOPES = auth.SCOPES


def candidate_sockets() -> List[str]:
    """Every place a Discord IPC socket can legitimately live.

    Flatpak Discord keeps its socket inside its own sandbox runtime dir, which
    flatpak symlinks to ``$XDG_RUNTIME_DIR/discord-ipc-0``; that symlink dangles
    whenever Discord is closed, so both forms are checked.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    paths: List[str] = []
    for index in range(10):
        paths.append(os.path.join(runtime, "discord-ipc-%d" % index))
    for index in range(10):
        paths.extend(sorted(glob.glob(os.path.join(runtime, "app", "*", "discord-ipc-%d" % index))))
    seen, unique = set(), []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def find_socket() -> Optional[str]:
    for path in candidate_sockets():
        try:
            if os.path.exists(path) and os.stat(path).st_mode:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(0.5)
                try:
                    probe.connect(path)
                    return path
                except OSError:
                    continue
                finally:
                    probe.close()
        except OSError:
            continue
    return None



_REDACT_KEYS = ("name", "nick", "username", "global_name", "topic")


def _redact(text: str) -> str:
    """Strip channel and member names before anything reaches the log.

    A verbose frame carries the whole voice-channel payload, including other
    people's display names, so a log pasted into a bug report would leak them.
    """
    import re
    for key in _REDACT_KEYS:
        text = re.sub(r"'%s':\s*'[^']*'" % key, "'%s': '<redacted>'" % key, text)
        text = re.sub(r'"%s":\s*"[^"]*"' % key, '"%s": "<redacted>"' % key, text)
    return text

class VoiceUser:
    __slots__ = ("id", "name", "avatar", "mute", "deaf", "self_mute",
                 "self_deaf", "video", "speaking")

    def __init__(self, **kwargs):
        self.id = str(kwargs.get("id") or "")
        self.name = str(kwargs.get("name") or "?")
        self.avatar = kwargs.get("avatar") or None
        self.mute = bool(kwargs.get("mute"))
        self.deaf = bool(kwargs.get("deaf"))
        self.self_mute = bool(kwargs.get("self_mute"))
        self.self_deaf = bool(kwargs.get("self_deaf"))
        self.video = bool(kwargs.get("video"))
        self.speaking = bool(kwargs.get("speaking"))

    def as_dict(self) -> Dict[str, Any]:
        return {key: getattr(self, key) for key in self.__slots__}


class DiscordClient(threading.Thread):
    """Polls the selected voice channel and streams snapshots to a callback."""

    def __init__(self, client_id: str = "0", poll_seconds: float = 2.0,
                 on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_status: Optional[Callable[[str, str], None]] = None,
                 access_token: str = "",
                 refresh_token: str = "",
                 on_token: Optional[Callable[[Dict[str, str]], None]] = None,
                 verbose: bool = False):
        super().__init__(name="chatterglass-discord", daemon=True)
        self.client_id = str(client_id or "0")
        self.poll_seconds = max(0.5, float(poll_seconds))
        self.access_token = access_token or ""
        self.refresh_token = refresh_token or ""
        self._on_token = on_token
        self._on_update = on_update or (lambda _state: None)
        self._on_status = on_status or (lambda _status, _detail: None)
        self.verbose = verbose
        self._stop = threading.Event()
        self._auth_requested = threading.Event()
        self._lock = threading.Lock()
        self._socket: Optional[socket.socket] = None
        self._buffer = b""
        self._speaking: Dict[str, float] = {}
        self._channel_id: Optional[str] = None
        self._subscribed: set = set()
        self._state: Dict[str, Any] = {"connected": False, "channel": None,
                                       "guild": None, "users": [], "status": "idle"}

    # -- public API ------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        try:
            if self._socket is not None:
                self._socket.close()
        except OSError:
            pass

    def authorise(self) -> None:
        """Ask the user-facing side to start the Allow flow."""
        self._auth_requested.set()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    # -- thread ----------------------------------------------------------
    def run(self) -> None:
        if not self.client_id.isdigit() or set(self.client_id) <= {"0"}:
            # Discord answers an unregistered id with op 2 / code 4000
            # "Invalid Client ID" and closes the socket, so don't spam it.
            self._set_status("app-id-missing", "set the application ID in Settings")
            return
        delay = 1.0
        while not self._stop.is_set():
            socket_path = find_socket()
            if socket_path is None:
                self._set_status("discord-not-running", "no Discord IPC socket found")
                self._stop.wait(delay)
                delay = min(delay * 2, 15.0)
                continue
            try:
                self._log("connecting to %s" % socket_path)
                self._set_status("connecting", socket_path)
                self._session(socket_path)
                delay = 1.0
            except AuthError as error:
                self._log("auth failed: %s" % error)
                self._set_status("auth-failed", str(error))
                self._clear_state()
                self._stop.wait(5.0)
            except Exception as error:  # noqa: BLE001 - never let the thread die
                self._log("session ended: %r" % (error,))
                self._set_status("disconnected", str(error))
                self._clear_state()
            finally:
                self._close_socket()
            self._stop.wait(delay)
            delay = min(delay * 2, 15.0)

    def _log(self, message: str) -> None:
        if self.verbose:
            print("[discord] %s" % _redact(message), flush=True)

    def _session(self, path: str) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(path)
        self._socket = sock
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})

        deadline = time.monotonic() + 8.0
        ready = False
        while not ready and time.monotonic() < deadline and not self._stop.is_set():
            frame = self._read_frame(timeout=0.5)
            if frame is None:
                continue
            opcode, payload = frame
            if opcode == OP_CLOSE:
                message = (payload or {}).get("message") or "Discord closed the connection"
                if "Client ID" in str(message):
                    self._set_status("app-id-rejected", str(message))
                raise ConnectionError(str(message))
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode == OP_FRAME and payload.get("evt") == "READY":
                ready = True
        if not ready:
            raise TimeoutError("no READY from Discord (is RPC allowed in Settings > Advanced?)")
        self._log("ready")

        # Don't surprise anybody with a Discord consent window on first run:
        # sit connected-but-idle until the user presses Connect.
        while (not self._stop.is_set() and not self.access_token
               and not self._auth_requested.is_set()):
            self._set_status("not-connected", "press Connect to Discord")
            frame = self._read_frame(timeout=0.5)
            if frame is not None:
                opcode, payload = frame
                if opcode == OP_CLOSE:
                    raise ConnectionError("Discord closed the connection")
                if opcode == OP_PING:
                    self._send(OP_PONG, payload)
        self._auth_requested.clear()
        self._ensure_authenticated()

        # Subscriptions need the channel id, so they happen in _apply_channel
        # once Discord reports which channel the user is in.
        self._set_status("connected", path)

        next_poll = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_poll:
                self._command("GET_SELECTED_VOICE_CHANNEL", {})
                next_poll = now + self.poll_seconds
            frame = self._read_frame(timeout=min(0.5, self.poll_seconds))
            if frame is None:
                self._expire_speaking()
                continue
            self._handle_frame(*frame)
            self._expire_speaking()

    # -- authentication --------------------------------------------------
    def _ensure_authenticated(self) -> None:
        if self.access_token:
            try:
                self._authenticate(self.access_token)
                return
            except AuthError as error:
                self._log("stored token rejected: %s" % error)
        if self.refresh_token:
            try:
                self._store_tokens(auth.refresh(self.client_id, self.refresh_token))
                self._authenticate(self.access_token)
                return
            except AuthError as error:
                self._log("refresh failed: %s" % error)
        # Last resort: the one-time browser authorise with PKCE.
        self._set_status("authorising", "finish Discord's page in your browser")
        self._store_tokens(auth.authorise(self.client_id))
        self._authenticate(self.access_token)
        self._log("authorised")

    def _store_tokens(self, payload: Dict[str, Any]) -> None:
        self.access_token = str(payload.get("access_token") or "")
        refresh = payload.get("refresh_token")
        if refresh:
            self.refresh_token = str(refresh)
        if self._on_token:
            self._on_token({"access_token": self.access_token,
                            "refresh_token": self.refresh_token})

    def _authenticate(self, token: str) -> None:
        self._send(OP_FRAME, {
            "cmd": "AUTHENTICATE",
            "args": {"access_token": token},
            "nonce": uuid.uuid4().hex,
        })
        self._wait_for_cmd("AUTHENTICATE", timeout=20.0)
        self._log("authenticated")

    def _wait_for_cmd(self, cmd: str, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stop.is_set():
            remaining = max(0.05, min(1.0, deadline - time.monotonic()))
            frame = self._read_frame(timeout=remaining)
            if frame is None:
                continue
            opcode, payload = frame
            if opcode == OP_CLOSE:
                raise ConnectionError("Discord closed the connection")
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode != OP_FRAME or payload.get("cmd") != cmd:
                continue
            if payload.get("evt") == "ERROR":
                data = payload.get("data") or {}
                raise AuthError(str(data.get("message") or "%s failed" % cmd))
            return payload
        raise AuthError("timed out waiting for %s" % cmd)

    # -- frames ----------------------------------------------------------
    def _send(self, opcode: int, payload: Dict[str, Any]) -> None:
        if self._socket is None:
            raise ConnectionError("not connected")
        data = json.dumps(payload).encode("utf-8")
        self._socket.sendall(struct.pack("<ii", opcode, len(data)) + data)

    def _command(self, cmd: str, args: Dict[str, Any], evt: Optional[str] = None) -> None:
        payload = {"cmd": cmd, "args": args or {}, "nonce": uuid.uuid4().hex}
        if evt:
            payload["evt"] = evt
        self._send(OP_FRAME, payload)

    def _recv_exact(self, size: int, deadline: float) -> Optional[bytes]:
        while len(self._buffer) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self._socket], [], [], remaining)
            if not ready:
                return None
            chunk = self._socket.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed")
            self._buffer += chunk
        chunk, self._buffer = self._buffer[:size], self._buffer[size:]
        return chunk

    def _read_frame(self, timeout: float):
        deadline = time.monotonic() + timeout
        header = self._recv_exact(8, deadline)
        if header is None:
            return None
        opcode, length = struct.unpack("<ii", header)
        if length < 0 or length > MAX_FRAME_BYTES:
            raise ConnectionError("frame length %d rejected (max %d)"
                                  % (length, MAX_FRAME_BYTES))
        body = self._recv_exact(length, deadline) if length else b"{}"
        if body is None:
            raise ConnectionError("truncated frame")
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            return None
        return opcode, payload

    def _handle_frame(self, opcode: int, payload: Dict[str, Any]) -> None:
        if self.verbose:
            self._log("frame op=%d cmd=%r evt=%r data=%r" % (
                opcode, payload.get("cmd"), payload.get("evt"),
                str(payload.get("data"))[:180],
            ))
        if opcode == OP_CLOSE:
            raise ConnectionError("Discord closed the connection")
        if opcode == OP_PING:
            self._send(OP_PONG, payload)
            return
        if opcode != OP_FRAME:
            return
        event = payload.get("evt")
        if event:
            self._handle_event(event, payload.get("data") or {})
            return
        cmd = payload.get("cmd")
        if cmd == "GET_SELECTED_VOICE_CHANNEL":
            error = payload.get("evt") or None
            if payload.get("data") is None and "error" in payload:
                self._log("voice channel error: %r" % (payload.get("error"),))
                self._clear_state()
                return
            self._apply_channel(payload.get("data"))
        elif cmd == "SUBSCRIBE" and "error" in payload:
            self._log("subscribe refused for %r" % (payload.get("error"),))

    def _subscribe_channel(self, channel_id: Optional[str]) -> None:
        """Discord wants cmd + evt + channel_id, not an args.event field."""
        if not channel_id or channel_id in self._subscribed:
            return
        for event in ("SPEAKING_START", "SPEAKING_STOP", "VOICE_STATE_CREATE",
                      "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE"):
            self._send(OP_FRAME, {
                "cmd": "SUBSCRIBE",
                "evt": event,
                "args": {"channel_id": channel_id},
                "nonce": uuid.uuid4().hex,
            })
        self._subscribed.add(channel_id)
        self._log("subscribed to %s" % channel_id)

    def _handle_event(self, event: str, data: Dict[str, Any]) -> None:
        if event == "SPEAKING_START":
            user_id = str(data.get("user_id") or "")
            if user_id:
                self._speaking[user_id] = time.monotonic()
                self._publish()
        elif event == "SPEAKING_STOP":
            user_id = str(data.get("user_id") or "")
            if user_id:
                self._speaking.pop(user_id, None)
                self._publish()
        elif event.startswith("VOICE_STATE_"):
            # Someone joined, left, muted or deafened: ask now instead of
            # waiting for the next poll.
            if event == "VOICE_STATE_DELETE":
                user_id = str(data.get("user_id") or "")
                if user_id:
                    self._speaking.pop(user_id, None)
            self._command("GET_SELECTED_VOICE_CHANNEL", {})
        elif event == "VOICE_CHANNEL_SELECT":
            self._command("GET_SELECTED_VOICE_CHANNEL", {})

    # Fight the Fairies - a dropped event must never leave a ring stuck on
    def _expire_speaking(self) -> None:
        """Safety net: a dropped SPEAKING_STOP must not leave a ring stuck on."""
        now = time.monotonic()
        stale = [uid for uid, seen in self._speaking.items() if now - seen > 30.0]
        for user_id in stale:
            self._speaking.pop(user_id, None)
        if stale:
            self._publish()

    # -- state -----------------------------------------------------------
    def _apply_channel(self, data: Optional[Dict[str, Any]]) -> None:
        if not data:
            self._clear_state()
            return
        guild = data.get("guild") or {}
        users: List[VoiceUser] = []
        for raw in data.get("voice_states") or []:
            user = raw.get("user") or {}
            # Discord nests the flags under "voice_state" and keeps the server
            # nickname separately at the top level.
            state = raw.get("voice_state") or {}
            users.append(VoiceUser(
                id=user.get("id"),
                name=(raw.get("nick") or user.get("global_name")
                      or user.get("username") or "?"),
                avatar=user.get("avatar"),
                mute=state.get("mute", raw.get("mute")),
                deaf=state.get("deaf", raw.get("deaf")),
                self_mute=state.get("self_mute"),
                self_deaf=state.get("self_deaf"),
                video=state.get("self_video"),
                speaking=str(user.get("id") or "") in self._speaking,
            ))
        with self._lock:
            self._state = {
                "connected": True,
                "status": "connected",
                "channel": data.get("name"),
                "guild": guild.get("name"),
                "users": [user.as_dict() for user in users],
            }
        self._log("in a voice channel with %d user(s)" % len(users))
        self._subscribe_channel(str(data.get("id") or "") or None)
        self._publish()

    def _publish(self) -> None:
        state = self.snapshot()
        for user in state.get("users", []):
            user["speaking"] = str(user.get("id")) in self._speaking
        state["connected"] = bool(state.get("channel"))
        with self._lock:
            self._state["users"] = state.get("users", [])
            self._state["connected"] = state["connected"]
        try:
            self._on_update(state)
        except Exception as error:  # noqa: BLE001
            self._log("update callback failed: %r" % (error,))

    def _clear_state(self) -> None:
        self._speaking.clear()
        with self._lock:
            self._state = {"connected": False, "status": "disconnected",
                           "channel": None, "guild": None, "users": []}
        try:
            self._on_update(self.snapshot())
        except Exception:
            pass

    def _set_status(self, status: str, detail: str) -> None:
        with self._lock:
            self._state["status"] = status
        try:
            self._on_status(status, detail)
        except Exception:
            pass

    def _close_socket(self) -> None:
        try:
            if self._socket is not None:
                self._socket.close()
        except OSError:
            pass
        self._socket = None
        self._buffer = b""
