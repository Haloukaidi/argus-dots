#!/usr/bin/env python3
"""Offline, conservative Argus dots source installer (Python 3.11+, POSIX).

No import of Argus, network access, native-agent claim, package installation,
credential lookup, or global configuration change is performed here.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import time
import uuid

STATE_NAME = ".argus-dots-install"
SCHEMA = 1


class InstallError(Exception):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative(value: str) -> str:
    p = PurePosixPath(value)
    if not isinstance(value, str) or not value or p.is_absolute() or "\\" in value or any(x in ("", ".", "..") for x in value.split("/")):
        raise InstallError(f"Unsafe relative path: {value!r}")
    if p.parts[0] == STATE_NAME:
        raise InstallError("Payload may not address installer metadata")
    return value


def no_symlink(path: Path) -> None:
    """Reject links in every existing component, including the supplied root."""
    path = Path(os.path.abspath(path))
    for p in [*reversed(path.parents), path]:
        if p.is_symlink():
            raise InstallError(f"Symlink path refused: {p}")


def target(root: Path, rel: str) -> Path:
    path = root / relative(rel)
    no_symlink(path)
    return path


def snapshot(path: Path) -> dict | None:
    no_symlink(path)
    if not path.exists():
        return None
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        raise InstallError(f"Expected a regular file: {path}")
    if st.st_nlink != 1:
        raise InstallError(f"Hard-linked file refused: {path}")
    return {"sha256": sha(path.read_bytes()), "mode": stat.S_IMODE(st.st_mode)}


def same(a: dict | None, b: dict | None) -> bool:
    return a == b


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    no_symlink(path)
    no_symlink(path.parent)
    fd, name = tempfile.mkstemp(prefix=".dots-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
        no_symlink(path)
        os.replace(name, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path: Path, value: dict) -> None:
    atomic_write(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def read_json(path: Path) -> dict:
    if snapshot(path) is None:
        raise InstallError(f"Missing metadata: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as e:
        raise InstallError(f"Invalid JSON: {path}") from e
    if not isinstance(data, dict):
        raise InstallError(f"Expected JSON object: {path}")
    return data


def validate_fingerprint(fp: dict | None) -> None:
    if fp is None:
        return
    if not isinstance(fp, dict) or set(fp) != {"sha256", "mode"}:
        raise InstallError("Invalid stored file fingerprint")
    if not isinstance(fp["sha256"], str) or len(fp["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in fp["sha256"]):
        raise InstallError("Invalid stored SHA256")
    if type(fp["mode"]) is not int or fp["mode"] < 0 or fp["mode"] > 0o777:
        raise InstallError("Unsafe stored file mode")


def validate_state(state: dict) -> None:
    if state.get("schema") != SCHEMA or not isinstance(state.get("files"), dict):
        raise InstallError("Unsupported installer state")
    for rel, info in state["files"].items():
        relative(rel)
        validate_fingerprint(info.get("installed"))
        validate_fingerprint(info.get("original"))
        backup = info.get("backup")
        if backup is not None:
            validate_backup_ref(backup)


def validate_backup_ref(value: str) -> str:
    relative(value)
    parts = value.split("/")
    if len(parts) != 4 or parts[0] != "transactions" or parts[2] != "before" or not parts[3].isdigit() or not parts[1] or any(c not in "0123456789-abcdef" for c in parts[1]):
        raise InstallError("Unsafe backup reference")
    return value


def empty_state() -> dict:
    return {"schema": SCHEMA, "release": None, "files": {}, "last_install": None}


class Installer:
    def __init__(self, root: Path, package: Path):
        if os.name != "posix":
            raise InstallError("This release requires POSIX. Native Windows is unsupported; WSL is unverified.")
        if sys.version_info < (3, 11):
            raise InstallError("Python 3.11 or newer is required")
        no_symlink(root)
        no_symlink(package)
        self.root = Path(os.path.abspath(root))
        self.package = Path(os.path.abspath(package))
        if not self.root.is_dir() or not (self.root / "pyproject.toml").is_file() or not (self.root / "argus").is_dir():
            raise InstallError("--argus-dir must be an existing compatible Argus source checkout, not site-packages or an empty directory")
        self.meta = self.root / STATE_NAME
        no_symlink(self.meta)
        self.manifest = read_json(self.package / "manifest.json")
        self.validate_package()
        self.state = empty_state()

    def validate_package(self) -> None:
        m = self.manifest
        if m.get("schema") != SCHEMA or not isinstance(m.get("base"), dict) or not isinstance(m.get("payload"), dict) or not m.get("release"):
            raise InstallError("Unsupported package manifest")
        for rel, digest in m["base"].items():
            relative(rel)
            validate_fingerprint({"sha256": digest, "mode": 0o644})
        for rel, info in m["payload"].items():
            relative(rel)
            validate_fingerprint(info)
            path = target(self.package / "payload", rel)
            if snapshot(path) is None or sha(path.read_bytes()) != info["sha256"]:
                raise InstallError(f"Package payload checksum mismatch: {rel}")
            if rel.endswith(".py"):
                ast.parse(path.read_bytes(), filename=rel)
        for rel, digest in m.get("legacy", {}).items():
            relative(rel)
            validate_fingerprint({"sha256": digest, "mode": 0o644})

        for release, files in m.get("upgrade_payloads", {}).items():
            if release not in m.get("upgrade_from", []) or not isinstance(files, dict) or not files:
                raise InstallError("Invalid known upgrade manifest")
            if set(files) - set(m["payload"]):
                raise InstallError("Upgrade removes known managed paths without a migration")
            for rel, digest in files.items():
                relative(rel)
                validate_fingerprint({"sha256": digest, "mode": 0o644})

    @contextlib.contextmanager
    def locked(self):
        import fcntl
        no_symlink(self.meta)
        if not self.meta.exists():
            self.meta.mkdir(mode=0o700)
            fsync_dir(self.root)
        st = self.meta.stat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise InstallError(f"Installer state must be a directory owned by you with mode 0700: {self.meta}")
        lock = self.meta / "lock"
        no_symlink(lock)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            lock_stat = os.fstat(fd)
            if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1 or lock_stat.st_uid != os.getuid() or stat.S_IMODE(lock_stat.st_mode) != 0o600:
                raise InstallError("Unsafe installer lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise InstallError("Another dots installer is active") from e
            if (self.meta / "state.json").exists():
                self.state = read_json(self.meta / "state.json")
                validate_state(self.state)
                self.validate_managed_paths(self.state["files"])
            yield
        finally:
            os.close(fd)

    def validate_managed_paths(self, entries: dict) -> None:
        if set(entries) - set(self.manifest["payload"]):
            raise InstallError("Stored metadata addresses paths outside this package; refusing recovery/mutation")

    def ensure_no_pending(self) -> None:
        if (self.meta / "pending.json").exists():
            raise InstallError("An interrupted transaction exists. Run --recover before any install/uninstall/rollback.")

    def conflict_check(self, expected: dict[str, dict | None]) -> None:
        errors = []
        for rel, wanted in expected.items():
            if not same(snapshot(target(self.root, rel)), wanted):
                errors.append(rel)
        if errors:
            raise InstallError("Files differ from the expected state; no changes made: " + ", ".join(errors[:12]))

    def validate_git_revision(self) -> None:
        """Read only Git identity metadata; never execute Git/config/hooks."""
        gitdir = self.root / ".git"
        no_symlink(gitdir)
        if not gitdir.exists():
            return  # Source archives are identified by the complete runtime manifest.
        if gitdir.is_file():
            snapshot(gitdir)
            pointer = gitdir.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir: "):
                raise InstallError("Malformed Git worktree metadata")
            gitdir = Path(os.path.abspath(self.root / pointer[8:]))
            no_symlink(gitdir)
        if not gitdir.is_dir():
            raise InstallError("Invalid Git metadata directory")
        def text_file(path):
            if snapshot(path) is None:
                raise InstallError("Missing Git identity metadata: " + str(path))
            return path.read_text(encoding="utf-8").strip()
        head = text_file(gitdir / "HEAD")
        if head.startswith("ref: "):
            ref = relative(head[5:])
            if not ref.startswith("refs/"):
                raise InstallError("Invalid Git HEAD reference")
            common = gitdir
            common_file = gitdir / "commondir"
            if common_file.exists():
                common = Path(os.path.abspath(gitdir / text_file(common_file)))
                no_symlink(common)
            value = None
            for metadata_root in dict.fromkeys((gitdir, common)):
                refpath = target(metadata_root, ref)
                if refpath.exists():
                    value = text_file(refpath)
                    break
                packed = metadata_root / "packed-refs"
                if packed.exists():
                    for line in text_file(packed).splitlines():
                        parts = line.split()
                        if len(parts) == 2 and parts[1] == ref:
                            value = parts[0]
                            break
                if value is not None:
                    break
            head = value
        if head != self.manifest["base_commit"]:
            raise InstallError("Git HEAD is not the supported fixed Argus baseline; archive hashes alone do not override an unknown Git revision")

    def compatibility(self, legacy: bool = False) -> None:
        self.validate_git_revision()
        expected = self.manifest["base"]
        known = self.manifest.get("legacy", {}) if legacy else {}
        failures = []
        for rel, base_hash in expected.items():
            fp = snapshot(target(self.root, rel))
            wanted = self.state["files"].get(rel, {}).get("installed", {}).get("sha256") or known.get(rel) or base_hash
            if fp is None or fp["sha256"] != wanted:
                failures.append(rel)
        # Newer/unknown Python modules also make the core version ambiguous.
        allowed = set(expected) | set(self.state["files"]) | set(known)
        for directory in ("argus", "argus_skill"):
            base = target(self.root, directory)
            if base.exists():
                for walk_root, dirs, files in os.walk(base, followlinks=False):
                    for name in dirs:
                        no_symlink(Path(walk_root) / name)
                    for name in files:
                        if name.endswith(".py"):
                            rel = (Path(walk_root) / name).relative_to(self.root).as_posix()
                            if rel not in allowed:
                                failures.append(rel)
        if failures:
            raise InstallError("Unsupported or locally modified Argus core; expected pinned base " + self.manifest["base_commit"] + ": " + ", ".join(failures[:12]))

    def read_backup(self, ref: str, expected: dict) -> bytes:
        path = target(self.meta, validate_backup_ref(ref))
        if snapshot(path) is None:
            raise InstallError("Missing backup: " + ref)
        data = path.read_bytes()
        if sha(data) != expected["sha256"]:
            raise InstallError("Backup checksum mismatch: " + ref)
        return data

    def verify_upgrade_origin(self) -> None:
        """Authenticate a known source snapshot against packaged hashes, not its label."""
        known = self.manifest.get("upgrade_payloads", {}).get(self.state["release"])
        if not known or set(self.state["files"]) != set(known):
            raise InstallError("Installed metadata does not describe the complete known previous release")
        for rel, digest in known.items():
            installed = self.state["files"][rel]["installed"]
            if installed is None or installed["sha256"] != digest:
                raise InstallError("Installed metadata differs from the known previous payload: " + rel)
        self.conflict_check({r: i["installed"] for r, i in self.state["files"].items()})
        last = self.journal(self.state.get("last_install"))
        if last["after_state"] != self.state or last.get("kind") not in ("install", "adopt_legacy") or set(last["entries"]) != set(known):
            raise InstallError("Previous installation journal is missing or inconsistent")
        for rel, item in last["entries"].items():
            if item["after"] != self.state["files"][rel]["installed"]:
                raise InstallError("Previous installation journal payload is inconsistent")
        # Previous releases were installed over the fixed base or the exact
        # legacy snapshot. Mixed/arbitrary original metadata is not trusted.
        origins = [self.manifest["base"]]
        if self.manifest.get("legacy"):
            origins.append({**self.manifest["base"], **self.manifest["legacy"]})
        if not any(all((info["original"]["sha256"] if info["original"] else None) == origin.get(rel)
                       for rel, info in self.state["files"].items()) for origin in origins):
            raise InstallError("Original backup metadata is not a recognized base or legacy snapshot")
        for info in self.state["files"].values():
            if info["original"] is None:
                if info["backup"] is not None:
                    raise InstallError("Unexpected backup for a newly created managed file")
            elif info["backup"] is None:
                raise InstallError("Missing original backup reference")
            else:
                self.read_backup(info["backup"], info["original"])

    def install(self, adopt_legacy: bool = False, check: bool = False, upgrade: bool = False) -> dict:
        self.ensure_no_pending()
        changing_release = bool(self.state["files"]) and self.state["release"] != self.manifest["release"]
        if changing_release:
            if self.state["release"] not in self.manifest.get("upgrade_from", []):
                raise InstallError("This installed release is not in this package's supported upgrade chain")
            self.verify_upgrade_origin()
            if not upgrade and not check:
                raise InstallError("Known previous installation verified; explicitly pass --upgrade to install this release")
        if adopt_legacy and self.state["files"]:
            raise InstallError("--adopt-legacy is only for a recognized unmanaged v1 installation")
        legacy = self.manifest.get("legacy", {}) if adopt_legacy else {}
        if adopt_legacy and not legacy:
            raise InstallError("No recognized legacy release in this package")
        if adopt_legacy:
            for rel, digest in legacy.items():
                fp = snapshot(target(self.root, rel))
                if fp is None or fp["sha256"] != digest:
                    raise InstallError("Legacy installation does not match the complete known snapshot: " + rel)
        self.compatibility(legacy=adopt_legacy)
        planned = {}
        new_state = copy.deepcopy(self.state)
        for rel, info in self.manifest["payload"].items():
            before = snapshot(target(self.root, rel))
            if rel in self.state["files"]:
                wanted = self.state["files"][rel]["installed"]
            elif rel in legacy:
                wanted = before
            elif rel in self.manifest["base"]:
                wanted = {"sha256": self.manifest["base"][rel], "mode": before["mode"]} if before else None
            else:
                wanted = None
            if not same(before, wanted):
                raise InstallError("Refusing to overwrite an unmanaged or modified file: " + rel)
            # Preserve an original target's ordinary permissions, never introduce executable/set-id bits.
            after = {"sha256": info["sha256"], "mode": before["mode"] if before else info["mode"]}
            validate_fingerprint(after)
            planned[rel] = (before, after, (self.package / "payload" / rel).read_bytes())
            if rel not in new_state["files"]:
                new_state["files"][rel] = {"installed": after, "original": before, "backup": None}
            else:
                new_state["files"][rel]["installed"] = after
        if set(self.state["files"]) - set(self.manifest["payload"]):
            raise InstallError("Release removes previously managed paths; explicit migration required")
        if self.state["release"] == self.manifest["release"]:
            if any(not same(before, after) for before, after, _ in planned.values()):
                raise InstallError("Same release has different contents; package/version mismatch")
            return {"status": "already_installed", "release": self.state["release"], "self_check": "hashes_and_python_syntax", "native_tools": "not_verified"}
        if check:
            return {"status": "compatible", "release": self.manifest["release"], "files": len(planned), "native_tools": "not_verified"}
        new_state["release"] = self.manifest["release"]
        return self.transaction("adopt_legacy" if adopt_legacy else "install", planned, new_state)

    def transaction(self, kind: str, planned: dict, new_state: dict) -> dict:
        self.conflict_check({r: p[0] for r, p in planned.items()})
        txn_id = str(int(time.time())) + "-" + uuid.uuid4().hex
        txns = self.meta / "transactions"
        no_symlink(txns)
        txns.mkdir(mode=0o700, exist_ok=True)
        fsync_dir(self.meta)
        txn = txns / txn_id
        txn.mkdir(mode=0o700)
        fsync_dir(txns)
        before_dir = txn / "before"
        before_dir.mkdir(mode=0o700)
        fsync_dir(txn)
        entries = {}
        created_dirs = []
        for i, (rel, (before, after, data)) in enumerate(planned.items()):
            validate_fingerprint(before)
            validate_fingerprint(after)
            ref = None
            if before is not None:
                ref = f"transactions/{txn_id}/before/{i}"
                atomic_write(self.meta / ref, target(self.root, rel).read_bytes())
                self.read_backup(ref, before)
            entries[rel] = {"before": before, "after": after, "backup": ref}
            if kind in ("install", "adopt_legacy") and rel not in self.state["files"]:
                new_state["files"][rel]["backup"] = ref
            path = target(self.root, rel).parent
            while path != self.root and not path.exists():
                part = path.relative_to(self.root).as_posix()
                if part not in created_dirs:
                    created_dirs.append(part)
                path = path.parent
        if kind in ("install", "adopt_legacy"):
            new_state["last_install"] = txn_id
        journal = {"schema": SCHEMA, "id": txn_id, "kind": kind, "before_state": self.state, "after_state": new_state, "entries": entries, "created_dirs": created_dirs}
        write_json(txn / "journal.json", journal)
        write_json(self.meta / "pending.json", {"id": txn_id})
        try:
            for directory in sorted(created_dirs, key=lambda x: x.count("/")):
                path = target(self.root, directory)
                path.mkdir(mode=0o755, exist_ok=True)
                fsync_dir(path.parent)
            for rel, (before, after, data) in planned.items():
                self.conflict_check({rel: before})
                path = target(self.root, rel)
                if after is None:
                    path.unlink()
                    fsync_dir(path.parent)
                else:
                    if sha(data) != after["sha256"]:
                        raise InstallError("Internal payload checksum mismatch")
                    atomic_write(path, data, after["mode"])
            self.conflict_check({r: p[1] for r, p in planned.items()})
            write_json(self.meta / "state.json", new_state)
            (self.meta / "pending.json").unlink()
            fsync_dir(self.meta)
            self.state = new_state
            return {"status": kind + "_complete", "release": new_state["release"], "files": len(entries), "self_check": "hashes_and_python_syntax", "native_tools": "not_verified"}
        except BaseException:
            try:
                self.recover()
            except BaseException as recovery_error:
                print("Recovery requires attention: " + str(recovery_error) + "; keep backups and run --recover", file=sys.stderr)
            raise

    def journal(self, txn_id: str) -> dict:
        if not isinstance(txn_id, str) or not txn_id or any(c not in "0123456789-abcdef" for c in txn_id):
            raise InstallError("Invalid transaction ID")
        value = read_json(target(self.meta, f"transactions/{txn_id}/journal.json"))
        if value.get("schema") != SCHEMA or value.get("id") != txn_id or not isinstance(value.get("entries"), dict):
            raise InstallError("Invalid transaction journal")
        validate_state(value["before_state"])
        validate_state(value["after_state"])
        self.validate_managed_paths(value["before_state"]["files"])
        self.validate_managed_paths(value["after_state"]["files"])
        self.validate_managed_paths(value["entries"])
        for rel, item in value["entries"].items():
            relative(rel)
            validate_fingerprint(item["before"])
            validate_fingerprint(item["after"])
            if item["before"] is not None:
                validate_backup_ref(item["backup"])
        allowed_dirs = {str(p) for r in value["entries"] for p in PurePosixPath(r).parents if str(p) != "."}
        for rel in value.get("created_dirs", []):
            relative(rel)
            if rel not in allowed_dirs:
                raise InstallError("Stored directory is outside managed paths")
        return value

    def recover(self) -> dict:
        pending = self.meta / "pending.json"
        if not pending.exists():
            return {"status": "nothing_to_recover"}
        journal = self.journal(read_json(pending)["id"])
        # Validate every file and every required backup BEFORE restoring any.
        restore = {}
        for rel, item in journal["entries"].items():
            current = snapshot(target(self.root, rel))
            if not same(current, item["before"]) and not same(current, item["after"]):
                raise InstallError("Recovery conflict; user changes preserved: " + rel)
            data = self.read_backup(item["backup"], item["before"]) if item["before"] else None
            restore[rel] = (current, item["before"], data)
        for rel, (current, before, data) in reversed(list(restore.items())):
            self.conflict_check({rel: current})
            path = target(self.root, rel)
            if same(current, before):
                continue
            if before is None:
                path.unlink()
                fsync_dir(path.parent)
            else:
                atomic_write(path, data, before["mode"])
        for rel in sorted(journal.get("created_dirs", []), key=lambda x: x.count("/"), reverse=True):
            path = target(self.root, rel)
            if path.exists():
                try:
                    path.rmdir()  # Empty directories only; never recurse or delete user content.
                except OSError:
                    pass
        write_json(self.meta / "state.json", journal["before_state"])
        pending.unlink()
        fsync_dir(self.meta)
        self.state = journal["before_state"]
        return {"status": "recovered_previous_state", "release": self.state["release"]}

    def uninstall(self) -> dict:
        self.ensure_no_pending()
        if not self.state["files"]:
            return {"status": "not_installed"}
        self.conflict_check({r: i["installed"] for r, i in self.state["files"].items()})
        planned = {}
        for rel, info in self.state["files"].items():
            original = info["original"]
            data = self.read_backup(info["backup"], original) if original else None
            planned[rel] = (info["installed"], original, data)
        return self.transaction("uninstall", planned, empty_state())

    def rollback(self) -> dict:
        self.ensure_no_pending()
        txn_id = self.state.get("last_install")
        if not txn_id:
            return {"status": "nothing_to_rollback"}
        old = self.journal(txn_id)
        if self.state != old["after_state"]:
            raise InstallError("Current state does not match the recorded installation")
        self.conflict_check({r: i["after"] for r, i in old["entries"].items()})
        planned = {}
        for rel, item in old["entries"].items():
            data = self.read_backup(item["backup"], item["before"]) if item["before"] else None
            planned[rel] = (item["after"], item["before"], data)
        return self.transaction("rollback", planned, old["before_state"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--argus-dir", required=True, type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="Validate compatibility/integrity without changing source (creates private lock metadata)")
    actions.add_argument("--uninstall", action="store_true", help="Restore only files managed by this installation; keep backups and all runtime data")
    actions.add_argument("--rollback", action="store_true", help="Restore the state before the latest successful installation/upgrade")
    actions.add_argument("--recover", action="store_true", help="Roll back an interrupted source transaction")
    parser.add_argument("--adopt-legacy", action="store_true", help="Explicitly migrate only the recognized original 8-file source patch; uninstall restores it")
    parser.add_argument("--upgrade", action="store_true", help="Explicitly upgrade a complete recognized managed release after checking its payload and original backups")
    args = parser.parse_args(argv)
    if args.adopt_legacy and (args.uninstall or args.rollback or args.recover):
        parser.error("--adopt-legacy is only valid with install or --check")
    if args.upgrade and (args.adopt_legacy or args.uninstall or args.rollback or args.recover):
        parser.error("--upgrade is only valid with install or --check and cannot be combined with --adopt-legacy")
    try:
        installer = Installer(args.argus_dir, Path(__file__).absolute().parent)
        with installer.locked():
            if args.recover:
                result = installer.recover()
            elif args.uninstall:
                result = installer.uninstall()
            elif args.rollback:
                result = installer.rollback()
            else:
                result = installer.install(args.adopt_legacy, args.check, args.upgrade)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (InstallError, OSError, ValueError, KeyError, TypeError, SyntaxError) as e:
        print("dots installer: " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
