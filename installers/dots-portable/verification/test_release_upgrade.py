"""Opt-in offline tests against exact historical packages and pinned Git bytes.

Set ARGUS_DOTS_BASE_REPO to a local repository containing the fixed original
commit and current source. Set ARGUS_DOTS_HISTORY_ROOT to directories named
by release, each containing installers/dots-portable. No network is used.
Historical 2.0.0/legacy payload bytes are not fabricated by this test suite.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
import install_dots as current  # noqa: E402

RELEASES = ("dots-portable-2.1.0", "dots-portable-2.1.1", "dots-portable-2.1.2")
BASE = "9cfe9129fd90511c3a1865844ec7dfda1b5d1008"
NEW_ORIGINALS = ("frontend/core/src/commands.ts", "tests/apps/test_cli_parser.py", "tests/test_architecture_invariants.py")


class ReleaseUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = os.environ.get("ARGUS_DOTS_BASE_REPO")
        history = os.environ.get("ARGUS_DOTS_HISTORY_ROOT")
        if not repo or not history:
            raise unittest.SkipTest("Set ARGUS_DOTS_BASE_REPO and ARGUS_DOTS_HISTORY_ROOT for real historical-byte verification")
        cls.repo = Path(repo)
        cls.history = Path(history)
        cls.manifest = json.loads((PACKAGE / "manifest.json").read_text())
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.template = Path(cls.temp.name) / "base"
        cls.template.mkdir()
        data = subprocess.check_output(["git", "archive", BASE, *cls.manifest["base"]], cwd=cls.repo)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            # All names came from the validated package manifest; Git emits no
            # symlinks for this fixed set of original regular files.
            for item in archive.getmembers():
                if item.issym() or item.islnk() or item.name.startswith("/") or ".." in Path(item.name).parts:
                    raise AssertionError("Unexpected archive member")
            archive.extractall(cls.template, filter="data")
        cls.previous = {}
        for release in RELEASES:
            package = cls.history / release / "installers/dots-portable"
            spec = importlib.util.spec_from_file_location(release.replace(".", "_"), package / "install_dots.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            manifest = json.loads((package / "manifest.json").read_text())
            if manifest["release"] != release:
                raise AssertionError("Wrong historical package identity")
            if {r: i["sha256"] for r, i in manifest["payload"].items()} != cls.manifest["upgrade_payloads"][release]:
                raise AssertionError("Historical package differs from approved exact mapping")
            cls.previous[release] = (module, package, manifest)

    def setUp(self):
        self.case = tempfile.TemporaryDirectory()
        self.addCleanup(self.case.cleanup)
        self.root = Path(self.case.name) / "source with spaces"
        shutil.copytree(self.template, self.root)
        (self.root / "README.dots.zh-CN.md").write_text("User-owned local notes\n")
        (self.root / "runtime").mkdir()
        (self.root / "runtime/user-state.json").write_text('{"preserve":true}')

    def invoke(self, action="install", release=None, **kwargs):
        module, package = (self.previous[release][:2] if release else (current, PACKAGE))
        instance = module.Installer(self.root, package)
        with instance.locked():
            return getattr(instance, action)(**kwargs)

    def assert_original(self):
        for rel, digest in self.manifest["base"].items():
            self.assertEqual(current.sha((self.root / rel).read_bytes()), digest, rel)
        for rel in set(self.manifest["payload"]) - set(self.manifest["base"]):
            self.assertFalse((self.root / rel).exists(), rel)
        self.assertEqual((self.root / "README.dots.zh-CN.md").read_text(), "User-owned local notes\n")
        self.assertEqual((self.root / "runtime/user-state.json").read_text(), '{"preserve":true}')

    def payload_snapshot(self):
        return {r: (self.root / r).read_bytes() if (self.root / r).is_file() else None for r in self.manifest["payload"]}

    def test_payload_equals_exact_source(self):
        ref = os.environ.get("ARGUS_DOTS_SOURCE_REF", "HEAD")
        for rel, info in self.manifest["payload"].items():
            data = subprocess.check_output(["git", "show", f"{ref}:{rel}"], cwd=self.repo)
            self.assertEqual(current.sha(data), info["sha256"], rel)
            self.assertEqual((PACKAGE / "payload" / rel).read_bytes(), data, rel)

    def test_expanded_baseline_and_older_maps_unchanged(self):
        old = self.previous[RELEASES[-1]][2]
        self.assertEqual(set(self.manifest["base"]) - set(old["base"]), set(NEW_ORIGINALS))
        for rel, digest in old["base"].items():
            self.assertEqual(self.manifest["base"][rel], digest)
        self.assertEqual(self.manifest["legacy"], old["legacy"])
        self.assertEqual(self.manifest["upgrade_payloads"]["dots-portable-2.0.0"], old["upgrade_payloads"]["dots-portable-2.0.0"])

    def test_fresh_repeat_uninstall(self):
        self.assertEqual(self.invoke()["status"], "install_complete")
        self.assertEqual(self.invoke()["status"], "already_installed")
        self.invoke("uninstall")
        self.assert_original()

    def test_each_exact_previous_release_upgrade_and_uninstall(self):
        for release in RELEASES:
            with self.subTest(release=release):
                self.invoke(release=release)
                self.invoke(upgrade=True)
                self.invoke("uninstall")
                self.assert_original()

    def test_multigeneration_rollback_recognized_by_old_installers(self):
        self.invoke(release=RELEASES[0])
        for release in RELEASES[1:]:
            self.invoke(release=release, upgrade=True)
        self.invoke(upgrade=True)
        for release in reversed(RELEASES):
            self.assertEqual(self.invoke("rollback")["release"], release)
            self.assertEqual(self.invoke(release=release, check=True)["status"], "already_installed")
        self.invoke("uninstall", release=RELEASES[0])
        self.assert_original()

    def test_new_original_backups_modes_and_restore(self):
        for rel in NEW_ORIGINALS:
            (self.root / rel).chmod(0o640)
        self.invoke(release=RELEASES[-1])
        self.invoke(upgrade=True)
        inst = current.Installer(self.root, PACKAGE)
        with inst.locked():
            for rel in NEW_ORIGINALS:
                info = inst.state["files"][rel]
                self.assertEqual(info["original"]["sha256"], self.manifest["base"][rel])
                self.assertEqual(info["original"]["mode"], 0o640)
                self.assertEqual(current.sha(inst.read_backup(info["backup"], info["original"])), self.manifest["base"][rel])
        self.invoke("uninstall")
        self.assert_original()
        for rel in NEW_ORIGINALS:
            self.assertEqual((self.root / rel).stat().st_mode & 0o777, 0o640)

    def test_new_original_local_edits_block_all_writes(self):
        self.invoke(release=RELEASES[-1])
        path = self.root / NEW_ORIGINALS[0]
        path.write_text("User's TypeScript changes\n")
        before = self.payload_snapshot()
        with self.assertRaises(current.InstallError):
            self.invoke(upgrade=True)
        self.assertEqual(self.payload_snapshot(), before)

    def test_postinstall_typescript_edit_blocks_uninstall(self):
        self.invoke()
        (self.root / NEW_ORIGINALS[0]).write_text("User's changes after install\n")
        before = self.payload_snapshot()
        with self.assertRaises(current.InstallError):
            self.invoke("uninstall")
        self.assertEqual(self.payload_snapshot(), before)

    def test_corrupt_old_original_backup_blocks_upgrade(self):
        self.invoke(release=RELEASES[-1])
        state = json.loads((self.root / current.STATE_NAME / "state.json").read_text())
        ref = next(i["backup"] for i in state["files"].values() if i["backup"])
        (self.root / current.STATE_NAME / ref).write_text("Corrupt backup")
        before = self.payload_snapshot()
        with self.assertRaises(current.InstallError):
            self.invoke(upgrade=True)
        self.assertEqual(self.payload_snapshot(), before)

    def test_forged_previous_metadata_does_not_authorize_upgrade(self):
        self.invoke(release=RELEASES[-1])
        p = self.root / current.STATE_NAME / "state.json"
        state = json.loads(p.read_text())
        rel = next(iter(state["files"]))
        altered = b"# user replacement\n"
        (self.root / rel).write_bytes(altered)
        state["files"][rel]["installed"]["sha256"] = current.sha(altered)
        p.write_text(json.dumps(state))
        with self.assertRaises(current.InstallError):
            self.invoke(upgrade=True)
        self.assertEqual((self.root / rel).read_bytes(), altered)

    def test_write_failure_after_replace_restores_prior_release(self):
        self.invoke(release=RELEASES[-1])
        before = self.payload_snapshot()
        real_write = current.atomic_write
        triggered = False
        def fail(path, data, mode=0o600):
            nonlocal triggered
            real_write(path, data, mode)
            if path == self.root / NEW_ORIGINALS[0] and not triggered:
                triggered = True
                raise OSError("Injected failure after replacement")
        with mock.patch.object(current, "atomic_write", side_effect=fail), self.assertRaises(OSError):
            self.invoke(upgrade=True)
        self.assertTrue(triggered)
        self.assertEqual(self.payload_snapshot(), before)
        self.assertEqual(self.invoke(release=RELEASES[-1], check=True)["status"], "already_installed")

    def test_interrupted_upgrade_explicit_recovery(self):
        self.invoke(release=RELEASES[-1])
        before = self.payload_snapshot()
        real_write = current.atomic_write
        def fail(path, data, mode=0o600):
            real_write(path, data, mode)
            if path == self.root / NEW_ORIGINALS[0]:
                raise OSError("Injected interrupted process after replacement")
        with mock.patch.object(current, "atomic_write", side_effect=fail), mock.patch.object(current.Installer, "recover", side_effect=OSError("Process stopped")), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(OSError):
            self.invoke(upgrade=True)
        self.assertTrue((self.root / current.STATE_NAME / "pending.json").exists())
        self.invoke("recover")
        self.assertEqual(self.payload_snapshot(), before)
        self.assertEqual(self.invoke(release=RELEASES[-1], check=True)["status"], "already_installed")


if __name__ == "__main__":
    unittest.main()
