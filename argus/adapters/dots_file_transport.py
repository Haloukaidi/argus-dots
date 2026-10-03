"""Private POSIX file queue for explicit, supervised dot-host handoffs.

There is no dispatcher, credential discovery, network listener or daemon here.
The host inspects a specific request and writes events using ``emit``. Queue
files are task data, never instructions granting permission to execute work.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .dots_backend import (
    MAX_PAYLOAD_BYTES,
    MAX_RESPONSE_BYTES,
    PROTOCOL_VERSION,
    TERMINAL_EVENTS,
    DotsBridgeError,
    DotsCapabilities,
    DotsRequest,
    encode_payload,
    validate_event,
    validate_request,
    validate_request_id,
)


class _BridgeBusy(DotsBridgeError):
    pass


class FileDotsTransport:
    """A private, non-resumable queue. Only the final root may be created.

    All access beneath the root is relative to pinned directory descriptors;
    symbolic links, traversal, non-regular files and non-private modes fail.
    POSIX is required rather than silently weakening these protections.
    """

    capabilities = DotsCapabilities()

    def tool_result(self, request_id: str, sequence: int, result: dict[str, Any]) -> None:
        raise DotsBridgeError("file bridge does not support call-bound role tools")

    def __init__(self, root: Path | str) -> None:
        path = Path(root)
        if ".." in path.parts:
            raise DotsBridgeError("bridge directory cannot contain '..'")
        self.root = path.absolute()
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise DotsBridgeError("the file dots bridge requires POSIX no-follow directory access")
        with self._root_fd():
            pass

    @staticmethod
    def _check_private(fd: int, *, directory: bool) -> None:
        info = os.fstat(fd)
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise DotsBridgeError("bridge entries must be private, owned by this user, and regular files/directories")
        if not directory and info.st_nlink != 1:
            raise DotsBridgeError("bridge files cannot be hard-linked")

    @contextmanager
    def _root_fd(self) -> Iterator[int]:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = os.open("/", flags)
        try:
            parts = self.root.parts[1:]
            if not parts:
                raise DotsBridgeError("filesystem root cannot be a bridge directory")
            for index, part in enumerate(parts):
                if index == len(parts) - 1:
                    try:
                        os.mkdir(part, 0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                next_fd = os.open(part, flags, dir_fd=current)
                os.close(current)
                current = next_fd
            self._check_private(current, directory=True)
            yield current
        finally:
            os.close(current)

    @contextmanager
    def _task_fd(self, request_id: str, *, create: bool = False) -> Iterator[int]:
        validate_request_id(request_id)
        with self._root_fd() as root_fd:
            if create:
                # Exclusive mkdir is the replay/collision guard. Existing IDs
                # are never overwritten, adopted, or automatically resumed.
                os.mkdir(request_id, 0o700, dir_fd=root_fd)
            fd = os.open(request_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                self._check_private(fd, directory=True)
                yield fd
            finally:
                os.close(fd)

    @contextmanager
    def _locked(self, fd: int, *, wait: bool = True, create: bool = True) -> Iterator[None]:
        import fcntl

        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0)
        lock = os.open("lock", flags, 0o600, dir_fd=fd)
        try:
            self._check_private(lock, directory=False)
            # Poll/read never wait on a stalled or abandoned host process.
            deadline = time.monotonic() + (0.5 if wait else 0)
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise _BridgeBusy("bridge request is busy; retry this explicit operation") from exc
                    time.sleep(0.005)
            yield
        finally:
            os.close(lock)

    @staticmethod
    def _write(fd: int, name: str, value: dict[str, Any], *, replace: bool = False) -> None:
        data = encode_payload(value)
        temporary = ".pending-" + uuid.uuid4().hex
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Linking publishes the complete file without replacing an existing
            # record. Readers hold the same task lock, so they never observe
            # the short-lived second hard link before its removal.
            if replace:
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            else:
                os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
        os.fsync(fd)

    @staticmethod
    def _read(fd: int, name: str) -> dict[str, Any] | None:
        try:
            source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            return None
        try:
            FileDotsTransport._check_private(source, directory=False)
            if os.fstat(source).st_size > MAX_PAYLOAD_BYTES:
                raise DotsBridgeError("bridge payload exceeds the 1 MiB safety limit")
            with os.fdopen(source, "rb", closefd=False) as stream:
                raw = stream.read(MAX_PAYLOAD_BYTES + 1)
            if len(raw) > MAX_PAYLOAD_BYTES:
                raise DotsBridgeError("bridge payload exceeds the 1 MiB safety limit")
        finally:
            os.close(source)

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise DotsBridgeError("duplicate JSON field")
                result[key] = value
            return result

        try:
            value = json.loads(raw, object_pairs_hook=unique_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(DotsBridgeError("non-finite JSON number")))
        except (ValueError, UnicodeError) as exc:
            raise DotsBridgeError("invalid bridge JSON: " + str(exc)) from exc
        if not isinstance(value, dict):
            raise DotsBridgeError("bridge payload must be a JSON object")
        return value

    @staticmethod
    def _request(fd: int, request_id: str) -> DotsRequest:
        request = validate_request(FileDotsTransport._read(fd, "request.json"))
        if request.request_id != request_id:
            raise DotsBridgeError("stored request identity does not match its directory")
        return request

    @staticmethod
    def _events(fd: int, request_id: str) -> list[dict[str, Any]]:
        names = sorted(name for name in os.listdir(fd) if name.startswith("event-"))
        if len(names) > 4096:
            raise DotsBridgeError("too many events in one bridge request")
        events = []
        total_bytes = 0
        accepted = terminal = False
        for sequence, name in enumerate(names, 1):
            if name != f"event-{sequence:08d}.json":
                raise DotsBridgeError("event files are out of sequence")
            event = validate_event(FileDotsTransport._read(fd, name), request_id, sequence)
            total_bytes += len(encode_payload(event))
            if total_bytes > MAX_RESPONSE_BYTES:
                raise DotsBridgeError("response exceeds the 8 MiB safety limit")
            if terminal:
                raise DotsBridgeError("event after terminal result")
            kind = event["type"]
            if kind == "accepted":
                if accepted:
                    raise DotsBridgeError("request already accepted")
                accepted = True
            if kind in {"message", "completed"} and not accepted:
                raise DotsBridgeError("host must accept the task before sending output")
            terminal = kind in TERMINAL_EVENTS
            events.append(event)
        return events

    @staticmethod
    def _state_for(events: list[dict[str, Any]], request_id: str) -> dict[str, Any]:
        history_digest = ""
        total_bytes = 0
        for event in events:
            data = encode_payload(event)
            total_bytes += len(data)
            history_digest = hashlib.sha256(history_digest.encode() + b"\0" + data).hexdigest()
        return {"protocol_version": PROTOCOL_VERSION, "request_id": request_id, "sequence": len(events),
                "bytes": total_bytes, "history_digest": history_digest,
                "accepted": any(e["type"] == "accepted" for e in events),
                "terminal": events[-1]["type"] if events and events[-1]["type"] in TERMINAL_EVENTS else "",
                "last_digest": hashlib.sha256(encode_payload(events[-1])).hexdigest() if events else ""}

    @staticmethod
    def _state(fd: int, request_id: str) -> dict[str, Any]:
        """Read the committed append head; recover only a partial publication.

        Events are immutable records. Under the request lock, publication is
        event-first, head-second. A crash between these leaves the next event
        present: validate the bounded whole log once before adopting its head.
        A changed/missing committed event is corruption, never a silent reset.
        """
        state = FileDotsTransport._read(fd, "head.json")
        if state is not None:
            if set(state) != {"protocol_version", "request_id", "sequence", "bytes", "accepted", "terminal", "last_digest", "history_digest"}:
                raise DotsBridgeError("invalid committed event head")
            if (type(state["protocol_version"]) is not int or state["protocol_version"] != PROTOCOL_VERSION
                    or state["request_id"] != request_id or type(state["sequence"]) is not int
                    or not 0 <= state["sequence"] <= 4096 or type(state["bytes"]) is not int
                    or not 0 <= state["bytes"] <= MAX_RESPONSE_BYTES or type(state["accepted"]) is not bool
                    or not isinstance(state["terminal"], str) or state["terminal"] not in ("", *TERMINAL_EVENTS)
                    or not isinstance(state["last_digest"], str)
                    or not isinstance(state["history_digest"], str)
                    or (state["sequence"] > 0 and (len(state["history_digest"]) != 64
                        or any(c not in "0123456789abcdef" for c in state["history_digest"])))):
                raise DotsBridgeError("invalid committed event head")
            sequence = state["sequence"]
            if sequence:
                last = validate_event(FileDotsTransport._read(fd, f"event-{sequence:08d}.json"), request_id, sequence)
                if hashlib.sha256(encode_payload(last)).hexdigest() != state["last_digest"]:
                    raise DotsBridgeError("committed event changed")
                if state["terminal"] != (last["type"] if last["type"] in TERMINAL_EVENTS else ""):
                    raise DotsBridgeError("committed terminal state mismatch")
                last_size = len(encode_payload(last))
                if (state["bytes"] < last_size or (sequence == 1 and state["bytes"] != last_size)
                        or (sequence == 1 and state["accepted"] != (last["type"] == "accepted"))
                        or (sequence > 1 and not state["accepted"])):
                    raise DotsBridgeError("committed event head is inconsistent")
            elif state != FileDotsTransport._state_for([], request_id):
                raise DotsBridgeError("invalid empty event head")
            if FileDotsTransport._read(fd, f"event-{sequence + 1:08d}.json") is None:
                return state
        events = FileDotsTransport._events(fd, request_id)
        if state is not None and state != FileDotsTransport._state_for(events[:state["sequence"]], request_id):
            raise DotsBridgeError("committed log prefix changed during recovery")
        state = FileDotsTransport._state_for(events, request_id)
        FileDotsTransport._write(fd, "head.json", state, replace=True)
        return state

    @staticmethod
    def _closed(fd: int) -> dict[str, Any] | None:
        value = FileDotsTransport._read(fd, "closed.json")
        if value is None:
            return None
        if value == {"status": "consumed"}:
            return value
        if (set(value) == {"status", "reason"} and value["status"] == "cancel_requested"
                and isinstance(value["reason"], str) and value["reason"].strip()):
            return value
        raise DotsBridgeError("invalid closed request record")

    def submit(self, request: DotsRequest) -> None:
        validate_request(request.to_dict())
        if request.expires_at <= time.time():
            raise DotsBridgeError("request already expired")
        with self._task_fd(request.request_id, create=True) as fd, self._locked(fd):
            self._write(fd, "request.json", request.to_dict())
            self._write(fd, "head.json", self._state_for([], request.request_id))

    def poll(self, request_id: str, sequence: int) -> dict[str, Any] | None:
        if type(sequence) is not int or sequence < 1 or sequence > 4096:
            raise DotsBridgeError("invalid event sequence")
        try:
            with self._task_fd(request_id) as fd, self._locked(fd, wait=False):
                self._request(fd, request_id)
                closed = self._closed(fd)
                if closed:
                    raise DotsBridgeError("request is closed; results cannot be replayed")
                state = self._state(fd, request_id)
                return self._read(fd, f"event-{sequence:08d}.json") if sequence <= state["sequence"] else None
        except _BridgeBusy:
            return None

    def cancel(self, request_id: str, reason: str) -> None:
        with self._task_fd(request_id) as fd, self._locked(fd):
            self._request(fd, request_id)
            if self._closed(fd) is None:
                self._write(fd, "closed.json", {"status": "cancel_requested", "reason": reason})

    def poll_cancellation(self, request_id: str) -> dict[str, Any] | None:
        """Observe an audited host stop acknowledgement without reopening results.

        A cancellation request alone proves nothing about the worker. Only a
        validated terminal ``cancelled`` event can acknowledge its actual stop.
        Pending or contended reads return promptly; late success stays closed.
        """
        try:
            with self._task_fd(request_id) as fd, self._locked(fd, wait=False):
                self._request(fd, request_id)
                closed = self._closed(fd)
                if closed is None or closed["status"] != "cancel_requested":
                    return None
                state = self._state(fd, request_id)
                if state["terminal"] != "cancelled":
                    return None
                events = self._events(fd, request_id)
                if state != self._state_for(events, request_id):
                    raise DotsBridgeError("committed cancellation log audit failed")
                return events[-1]
        except _BridgeBusy:
            return None

    def finish(self, request_id: str) -> None:
        with self._task_fd(request_id) as fd, self._locked(fd):
            self._request(fd, request_id)
            if self._closed(fd) is not None:
                raise DotsBridgeError("request closed before completion")
            state = self._state(fd, request_id)
            if not state["terminal"]:
                raise DotsBridgeError("cannot consume a request without a terminal event")
            # Audit the whole bounded immutable log once at consumption. This
            # detects gaps or changed history without O(n²) append parsing.
            if state != self._state_for(self._events(fd, request_id), request_id):
                raise DotsBridgeError("committed log audit failed")
            self._write(fd, "closed.json", {"status": "consumed"})

    def inspect(self, request_id: str) -> dict[str, Any]:
        """Read one known request; this does not claim, approve or dispatch it."""
        with self._task_fd(request_id) as fd, self._locked(fd):
            request = self._request(fd, request_id)
            return {"request": request.to_dict(), "events": self._events(fd, request_id),
                    "closed": self._closed(fd), "expired": time.time() >= request.expires_at}

    def emit(self, request_id: str, kind: str, *, text: str | None = None,
             worker_id: str | None = None, usage: dict[str, int] | None = None) -> dict[str, Any]:
        """Record one real host event, after separately reviewing authorization.

        An expired/cancelled request can only receive a cancellation acknowledgement.
        The host must actually stop its worker before recording ``cancelled``.
        """
        with self._task_fd(request_id) as fd, self._locked(fd):
            with self._root_fd() as root:
                if self._read(root, "admission-" + request_id + ".json") is not None:
                    raise DotsBridgeError("bounded producer request requires its host API; raw emit is disabled")
            if self._read(fd, "host-claim.json") is not None:
                raise DotsBridgeError("managed request requires its coordinator API; raw emit is disabled")
            return self._emit_locked(fd, request_id, kind, text=text, worker_id=worker_id, usage=usage)

    def _emit_locked(self, fd: int, request_id: str, kind: str, *, text: str | None = None,
                     worker_id: str | None = None, usage: dict[str, int] | None = None,
                     event_fields: dict[str, Any] | None = None) -> dict[str, Any]:
        """Append with the caller holding the request lock (also used by the host coordinator)."""
        request = self._request(fd, request_id)
        closed = self._closed(fd)
        state = self._state(fd, request_id)
        if state["terminal"]:
            raise DotsBridgeError("request already has a terminal event")
        if closed is not None or time.time() >= request.expires_at:
            if kind != "cancelled" or (closed is not None and closed.get("status") != "cancel_requested"):
                raise DotsBridgeError("request closed or expired; reject late work/results")
        if kind == "accepted" and state["accepted"]:
            raise DotsBridgeError("request already accepted")
        if kind in {"message", "completed"} and not state["accepted"]:
            raise DotsBridgeError("host must accept the task before sending output")
        event: dict[str, Any] = {"protocol_version": PROTOCOL_VERSION, "request_id": request_id,
                                 "sequence": state["sequence"] + 1, "type": kind}
        if text is not None:
            event["text"] = text
        if worker_id is not None:
            event["worker_id"] = worker_id
        if usage is not None:
            event["usage"] = usage
        if event_fields:
            if set(event_fields) & set(event):
                raise DotsBridgeError("extra event fields cannot replace the envelope")
            event.update(event_fields)
        validate_event(event, request_id, state["sequence"] + 1)
        event_bytes = encode_payload(event)
        if state["bytes"] + len(event_bytes) > MAX_RESPONSE_BYTES:
            raise DotsBridgeError("response exceeds the 8 MiB safety limit")
        if state["sequence"] >= 4096:
            raise DotsBridgeError("too many events in one bridge request")
        self._write(fd, f"event-{state['sequence'] + 1:08d}.json", event)
        self._write(fd, "head.json", {
            "protocol_version": PROTOCOL_VERSION, "request_id": request_id, "sequence": state["sequence"] + 1,
            "bytes": state["bytes"] + len(event_bytes), "accepted": state["accepted"] or kind == "accepted",
            "terminal": kind if kind in TERMINAL_EVENTS else "",
            "last_digest": hashlib.sha256(event_bytes).hexdigest(),
            "history_digest": hashlib.sha256(state["history_digest"].encode() + b"\0" + event_bytes).hexdigest(),
        }, replace=True)
        return event
