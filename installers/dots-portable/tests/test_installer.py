"""Standard-library offline fixtures. Does not claim native or macOS validation."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_dots as m
import bootstrap_argus as bootstrap
import check_host


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)
        self.root = self.top / "argus"
        self.package = self.top / "package"
        self.root.mkdir()
        self.package.mkdir()
        self.base = {"pyproject.toml": b'[project]\nname="argus"\n', "argus/__init__.py": b'"""Fixture."""\n', "argus/apps/entry.py": b"BASE = 1\n", "argus/reviewer/tools.py": b"BASE = 2\n"}
        self.payload = {"argus/apps/entry.py": b"DOTS = 1\n", "argus/reviewer/tools.py": b"DOTS = 2\n", "argus/adapters/dots_backend.py": b"DOTS = 3\n", "docs/dots.md": b"Module guide\n"}
        for rel, data in self.base.items():
            self.put(self.root, rel, data)
        for rel, data in self.payload.items():
            self.put(self.package / "payload", rel, data)
        self.manifest = {"schema": 1, "release": "v2", "base_commit": "a" * 40, "base": {r: m.sha(d) for r, d in self.base.items()}, "payload": {r: {"sha256": m.sha(d), "mode": 0o644} for r, d in self.payload.items()}, "legacy": {}, "upgrade_from": ["v1"]}
        self.save_manifest()

    def put(self, root, rel, data):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(0o644)

    def save_manifest(self):
        (self.package / "manifest.json").write_text(json.dumps(self.manifest))

    @contextlib.contextmanager
    def installer(self):
        inst = m.Installer(self.root, self.package)
        with inst.locked():
            yield inst

    def install(self, **kwargs):
        with self.installer() as inst:
            return inst.install(**kwargs)

    def assert_base(self):
        for rel, data in self.base.items():
            self.assertEqual((self.root / rel).read_bytes(), data)
        for rel in self.payload.keys() - self.base.keys():
            self.assertFalse((self.root / rel).exists())

    def test_fresh_idempotent_uninstall(self):
        self.assertEqual(self.install()["status"], "install_complete")
        self.assertEqual(self.install()["status"], "already_installed")
        with self.installer() as inst:
            self.assertEqual(inst.uninstall()["status"], "uninstall_complete")
            self.assertEqual(inst.uninstall()["status"], "not_installed")
        self.assert_base()
        self.assertTrue((self.root / m.STATE_NAME / "transactions").is_dir())

    def test_check_does_not_modify_source(self):
        with self.installer() as inst:
            self.assertEqual(inst.install(check=True)["status"], "compatible")
        self.assert_base()

    def test_modified_baseline_preflight(self):
        self.put(self.root, "argus/apps/entry.py", b"USER = 1\n")
        with self.assertRaises(m.InstallError):
            self.install()
        self.assertEqual((self.root / "argus/apps/entry.py").read_bytes(), b"USER = 1\n")
        self.assertFalse((self.root / "docs/dots.md").exists())

    def test_unknown_core_file(self):
        self.put(self.root, "argus/unknown.py", b"NEW = 1\n")
        with self.assertRaises(m.InstallError):
            self.install()

    def test_unmanaged_payload_conflict(self):
        self.put(self.root, "docs/dots.md", b"USER DOC\n")
        with self.assertRaises(m.InstallError):
            self.install()
        self.assertEqual((self.root / "argus/apps/entry.py").read_bytes(), self.base["argus/apps/entry.py"])

    def test_user_edits_block_uninstall_all_or_none(self):
        self.install()
        self.put(self.root, "docs/dots.md", b"USER DOC\n")
        with self.installer() as inst, self.assertRaises(m.InstallError):
            inst.uninstall()
        self.assertEqual((self.root / "argus/apps/entry.py").read_bytes(), self.payload["argus/apps/entry.py"])

    def test_uninstall_rejects_missing_managed_entry(self):
        self.install()
        self.edit_state(lambda state: state["files"].pop("docs/dots.md"))
        with self.installer() as inst, self.assertRaisesRegex(m.InstallError, "complete metadata"):
            inst.uninstall()
        for rel, data in self.payload.items():
            self.assertEqual((self.root / rel).read_bytes(), data)

    def test_uninstall_rejects_empty_active_state(self):
        self.install()
        self.edit_state(lambda state: state["files"].clear())
        with self.installer() as inst, self.assertRaisesRegex(m.InstallError, "incomplete"):
            inst.uninstall()
        for rel, data in self.payload.items():
            self.assertEqual((self.root / rel).read_bytes(), data)

    def test_uninstall_rejects_consistently_incomplete_journal_and_state(self):
        self.install()
        with self.installer() as inst:
            state = copy.deepcopy(inst.state)
            state["files"].pop("docs/dots.md")
            path = inst.meta / "transactions" / state["last_install"] / "journal.json"
            journal = json.loads(path.read_text())
            journal["after_state"] = state
            journal["entries"].pop("docs/dots.md")
            path.write_text(json.dumps(journal))
            (inst.meta / "state.json").write_text(json.dumps(state))
        with self.installer() as inst, self.assertRaisesRegex(m.InstallError, "complete metadata"):
            inst.uninstall()
        for rel, data in self.payload.items():
            self.assertEqual((self.root / rel).read_bytes(), data)

    def test_uninstall_rejects_inconsistent_original_journal(self):
        self.install()
        with self.installer() as inst:
            path = inst.meta / "transactions" / inst.state["last_install"] / "journal.json"
            journal = json.loads(path.read_text())
            journal["after_state"]["release"] = "different-release"
            path.write_text(json.dumps(journal))
        with self.installer() as inst, self.assertRaisesRegex(m.InstallError, "journal"):
            inst.uninstall()
        for rel, data in self.payload.items():
            self.assertEqual((self.root / rel).read_bytes(), data)

    def test_unrelated_runtime_and_docs_preserved(self):
        self.put(self.root, "runtime/user-state.json", b'{"keep":true}')
        self.put(self.root, "docs/user.md", b"keep")
        self.install()
        with self.installer() as inst:
            inst.uninstall()
        self.assertEqual((self.root / "runtime/user-state.json").read_bytes(), b'{"keep":true}')
        self.assertEqual((self.root / "docs/user.md").read_bytes(), b"keep")

    def test_atomic_failure_rolls_back(self):
        original = m.atomic_write
        failed = False
        def fail(path, data, mode=0o600):
            nonlocal failed
            if path == self.root / "argus/reviewer/tools.py" and not failed:
                failed = True
                raise OSError("injected disk failure")
            return original(path, data, mode)
        with mock.patch.object(m, "atomic_write", side_effect=fail):
            with self.assertRaises(OSError):
                self.install()
        self.assert_base()
        self.assertFalse((self.root / m.STATE_NAME / "pending.json").exists())

    def crash_transaction(self):
        original = m.atomic_write
        def crash(path, data, mode=0o600):
            original(path, data, mode)
            if path == self.root / "argus/reviewer/tools.py":
                raise OSError("power loss simulation after replace")
        with mock.patch.object(m, "atomic_write", side_effect=crash), mock.patch.object(m.Installer, "recover", side_effect=OSError("process ended")), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(OSError):
                self.install()
        self.assertTrue((self.root / m.STATE_NAME / "pending.json").exists())

    def test_recover_crash_after_replace(self):
        self.crash_transaction()
        with self.installer() as inst:
            with self.assertRaises(m.InstallError):
                inst.install()
            self.assertEqual(inst.recover()["status"], "recovered_previous_state")
            self.assertEqual(inst.recover()["status"], "nothing_to_recover")
        self.assert_base()

    def test_recover_conflict_preserves_every_file(self):
        self.crash_transaction()
        self.put(self.root, "argus/apps/entry.py", b"USER = 9\n")
        with self.installer() as inst, self.assertRaises(m.InstallError):
            inst.recover()
        self.assertEqual((self.root / "argus/reviewer/tools.py").read_bytes(), self.payload["argus/reviewer/tools.py"])
        self.assertEqual((self.root / "argus/apps/entry.py").read_bytes(), b"USER = 9\n")

    def test_corrupt_backup_blocks_uninstall(self):
        self.install()
        with self.installer() as inst:
            ref = inst.state["files"]["argus/apps/entry.py"]["backup"]
            (inst.meta / ref).write_bytes(b"corrupt")
            with self.assertRaises(m.InstallError):
                inst.uninstall()
        self.assertEqual((self.root / "docs/dots.md").read_bytes(), self.payload["docs/dots.md"])

    def test_symlink_root(self):
        link = self.top / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(m.InstallError):
            m.Installer(link, self.package)

    def test_symlink_parent(self):
        outside = self.top / "outside"
        outside.mkdir()
        (self.root / "docs").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(m.InstallError):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_target_and_metadata(self):
        external = self.top / "external"
        external.write_text("safe")
        p = self.root / "argus/apps/entry.py"
        p.unlink()
        p.symlink_to(external)
        with self.assertRaises(m.InstallError):
            self.install()
        self.assertEqual(external.read_text(), "safe")
        p.unlink()
        self.put(self.root, "argus/apps/entry.py", self.base["argus/apps/entry.py"])
        meta = self.root / m.STATE_NAME
        if meta.exists():
            shutil.rmtree(meta)
        meta.symlink_to(self.top / "outside", target_is_directory=True)
        with self.assertRaises(m.InstallError):
            self.install()

    def test_hardlink_rejected(self):
        p = self.root / "argus/apps/entry.py"
        os.link(p, self.top / "other")
        with self.assertRaises(m.InstallError):
            self.install()

    def test_fifo_backup_rejected_without_read(self):
        self.install()
        with self.installer() as inst:
            ref = inst.state["files"]["argus/apps/entry.py"]["backup"]
            path = inst.meta / ref
            path.unlink()
            os.mkfifo(path)
            with self.assertRaises(m.InstallError):
                inst.uninstall()

    def test_fifo_lock_rejected(self):
        meta = self.root / m.STATE_NAME
        meta.mkdir(mode=0o700)
        os.mkfifo(meta / "lock")
        with self.assertRaises(m.InstallError):
            self.install()

    def test_payload_corruption_rejected(self):
        (self.package / "payload/argus/apps/entry.py").write_bytes(b"changed")
        with self.assertRaises(m.InstallError):
            self.install()
        self.assert_base()

    def test_wrong_directory(self):
        empty = self.top / "empty"
        empty.mkdir()
        with self.assertRaises(m.InstallError):
            m.Installer(empty, self.package)
        self.assertEqual(list(empty.iterdir()), [])

    def test_unknown_git_head_rejected(self):
        self.put(self.root, ".git/HEAD", ("b" * 40 + "\n").encode())
        with self.assertRaises(m.InstallError):
            self.install()
        self.assert_base()

    def test_matching_detached_git_head(self):
        self.put(self.root, ".git/HEAD", ("a" * 40 + "\n").encode())
        self.assertEqual(self.install()["status"], "install_complete")

    def test_matching_packed_git_ref(self):
        self.put(self.root, ".git/HEAD", b"ref: refs/heads/main\n")
        self.put(self.root, ".git/packed-refs", ("a" * 40 + " refs/heads/main\n").encode())
        self.assertEqual(self.install()["status"], "install_complete")

    def test_matching_worktree_git_metadata(self):
        meta = self.top / "git-main/worktrees/example"
        self.put(meta, "HEAD", b"ref: refs/heads/main\n")
        self.put(meta, "commondir", b"../..\n")
        self.put(self.top / "git-main", "refs/heads/main", ("a" * 40 + "\n").encode())
        self.put(self.root, ".git", ("gitdir: " + str(meta) + "\n").encode())
        self.assertEqual(self.install()["status"], "install_complete")

    def test_native_windows_rejected(self):
        with mock.patch.object(m.os, "name", "nt"), self.assertRaises(m.InstallError):
            m.Installer(self.root, self.package)

    def test_preserves_original_modes(self):
        (self.root / "argus/apps/entry.py").chmod(0o640)
        self.install()
        self.assertEqual((self.root / "argus/apps/entry.py").stat().st_mode & 0o777, 0o640)
        with self.installer() as inst:
            inst.uninstall()
        self.assertEqual((self.root / "argus/apps/entry.py").stat().st_mode & 0o777, 0o640)

    def test_upgrade_and_rollback_then_uninstall(self):
        self.manifest["release"] = "v1"
        self.save_manifest()
        self.install()
        self.manifest["upgrade_payloads"] = {"v1": {r: i["sha256"] for r, i in self.manifest["payload"].items()}}
        self.manifest["release"] = "v2"
        self.payload["argus/adapters/dots_backend.py"] = b"DOTS = 4\n"
        self.put(self.package / "payload", "argus/adapters/dots_backend.py", self.payload["argus/adapters/dots_backend.py"])
        self.manifest["payload"]["argus/adapters/dots_backend.py"]["sha256"] = m.sha(self.payload["argus/adapters/dots_backend.py"])
        self.save_manifest()
        self.assertEqual(self.install(upgrade=True)["release"], "v2")
        with self.installer() as inst:
            self.assertEqual(inst.rollback()["release"], "v1")
            inst.uninstall()
        self.assert_base()

    def prepare_upgrade(self):
        self.base["argus/core/fix.py"] = b"ORIGINAL = 1\n"
        self.put(self.root, "argus/core/fix.py", self.base["argus/core/fix.py"])
        self.manifest["base"]["argus/core/fix.py"] = m.sha(self.base["argus/core/fix.py"])
        self.manifest["release"] = "v1"
        self.save_manifest()
        self.install()
        self.manifest["upgrade_payloads"] = {"v1": {r: i["sha256"] for r, i in self.manifest["payload"].items()}}
        self.manifest["release"] = "v2"
        for rel, data in {"argus/core/fix.py": b"FIXED = 1\n", "tests/new_test.py": b"TEST = 1\n"}.items():
            self.payload[rel] = data
            self.put(self.package / "payload", rel, data)
            self.manifest["payload"][rel] = {"sha256": m.sha(data), "mode": 0o644}
        self.save_manifest()

    def edit_state(self, edit):
        path = self.root / m.STATE_NAME / "state.json"
        state = json.loads(path.read_text())
        edit(state)
        path.write_text(json.dumps(state))

    def test_upgrade_requires_explicit_flag_and_check_is_readonly(self):
        self.prepare_upgrade()
        with self.assertRaisesRegex(m.InstallError, "--upgrade"):
            self.install()
        self.assertEqual(self.install(check=True)["status"], "compatible")
        self.assertEqual((self.root / "argus/core/fix.py").read_bytes(), b"ORIGINAL = 1\n")
        self.assertFalse((self.root / "tests/new_test.py").exists())

    def test_upgrade_added_original_backup_and_uninstall(self):
        self.prepare_upgrade()
        self.install(upgrade=True)
        with self.installer() as inst:
            info = inst.state["files"]["argus/core/fix.py"]
            self.assertEqual(inst.read_backup(info["backup"], info["original"]), b"ORIGINAL = 1\n")
            inst.uninstall()
        self.assert_base()

    def test_upgrade_added_paths_rollback_then_uninstall(self):
        self.prepare_upgrade()
        self.install(upgrade=True)
        with self.installer() as inst:
            self.assertEqual(inst.rollback()["release"], "v1")
            self.assertEqual((self.root / "argus/core/fix.py").read_bytes(), b"ORIGINAL = 1\n")
            self.assertFalse((self.root / "tests/new_test.py").exists())
            inst.uninstall()
        self.assert_base()

    def test_upgrade_rejects_partial_metadata(self):
        self.prepare_upgrade()
        self.edit_state(lambda state: state["files"].pop("docs/dots.md"))
        with self.assertRaisesRegex(m.InstallError, "complete known"):
            self.install(upgrade=True)

    def test_upgrade_rejects_forged_version_and_hashes(self):
        self.prepare_upgrade()
        data = b"USER = 9\n"
        self.put(self.root, "argus/apps/entry.py", data)
        self.edit_state(lambda state: state["files"]["argus/apps/entry.py"]["installed"].update(sha256=m.sha(data)))
        with self.assertRaisesRegex(m.InstallError, "known previous payload"):
            self.install(upgrade=True)
        self.assertEqual((self.root / "argus/apps/entry.py").read_bytes(), data)

    def test_upgrade_rejects_modified_old_payload(self):
        self.prepare_upgrade()
        self.put(self.root, "docs/dots.md", b"USER DOC")
        with self.assertRaises(m.InstallError):
            self.install(upgrade=True)
        self.assertEqual((self.root / "argus/core/fix.py").read_bytes(), b"ORIGINAL = 1\n")

    def test_upgrade_rejects_missing_or_corrupt_original_backup(self):
        self.prepare_upgrade()
        with self.installer() as inst:
            ref = inst.state["files"]["argus/apps/entry.py"]["backup"]
            (inst.meta / ref).write_bytes(b"CORRUPT")
        with self.assertRaisesRegex(m.InstallError, "Backup checksum"):
            self.install(upgrade=True)

    def test_upgrade_rejects_modified_journal(self):
        self.prepare_upgrade()
        with self.installer() as inst:
            path = inst.meta / "transactions" / inst.state["last_install"] / "journal.json"
            journal = json.loads(path.read_text())
            journal["after_state"]["release"] = "forged"
            path.write_text(json.dumps(journal))
        with self.assertRaisesRegex(m.InstallError, "journal"):
            self.install(upgrade=True)

    def test_upgrade_rejects_unknown_original_metadata(self):
        self.prepare_upgrade()
        with self.installer() as inst:
            state = copy.deepcopy(inst.state)
            state["files"]["argus/apps/entry.py"]["original"]["sha256"] = "0" * 64
            path = inst.meta / "transactions" / state["last_install"] / "journal.json"
            journal = json.loads(path.read_text())
            journal["after_state"] = state
            path.write_text(json.dumps(journal))
            (inst.meta / "state.json").write_text(json.dumps(state))
        with self.assertRaisesRegex(m.InstallError, "Original backup metadata"):
            self.install(upgrade=True)

    def test_upgrade_partial_write_failure_restores_old_release(self):
        self.prepare_upgrade()
        original = m.atomic_write
        failed = False
        def fail(path, data, mode=0o600):
            nonlocal failed
            if path == self.root / "tests/new_test.py" and not failed:
                failed = True
                raise OSError("injected upgrade write failure")
            return original(path, data, mode)
        with mock.patch.object(m, "atomic_write", side_effect=fail), self.assertRaises(OSError):
            self.install(upgrade=True)
        with self.installer() as inst:
            self.assertEqual(inst.state["release"], "v1")
            self.assertEqual((self.root / "argus/core/fix.py").read_bytes(), b"ORIGINAL = 1\n")
            self.assertFalse((self.root / "tests/new_test.py").exists())
            inst.uninstall()
        self.assert_base()

    def test_unknown_upgrade_rejected(self):
        self.manifest["release"] = "v0"
        self.save_manifest()
        self.install()
        self.manifest["release"] = "v2"
        self.save_manifest()
        with self.assertRaises(m.InstallError):
            self.install()

    def test_legacy_explicit_adoption_restores_original(self):
        legacy = {"argus/apps/entry.py": b"OLD = 1\n", "argus/reviewer/tools.py": b"OLD = 2\n", "argus/adapters/dots_backend.py": b"OLD = 3\n"}
        for rel, data in legacy.items():
            self.put(self.root, rel, data)
        self.manifest["legacy"] = {r: m.sha(d) for r, d in legacy.items()}
        self.save_manifest()
        with self.assertRaises(m.InstallError):
            self.install()
        with self.installer() as inst:
            inst.install(adopt_legacy=True)
            inst.uninstall()
        for rel, data in legacy.items():
            self.assertEqual((self.root / rel).read_bytes(), data)
        self.assertFalse((self.root / "docs/dots.md").exists())

    def test_malicious_metadata_outside_payload_refused(self):
        self.install()
        path = self.root / m.STATE_NAME / "state.json"
        data = json.loads(path.read_text())
        data["files"]["runtime/data.json"] = copy.deepcopy(next(iter(data["files"].values())))
        path.write_text(json.dumps(data))
        with self.assertRaises(m.InstallError):
            with self.installer():
                pass

    def test_lock_contention(self):
        with self.installer():
            with self.assertRaises(m.InstallError):
                self.install()

    def test_bootstrap_requires_explicit_network(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit), mock.patch.object(bootstrap.subprocess, "run") as run:
            bootstrap.main(["--target", str(self.top / "new")])
        run.assert_not_called()

    def test_host_missing_capabilities_fails(self):
        result = check_host.check([])
        self.assertEqual(result["inventory_check"], "failed")
        self.assertFalse(result["native_execution_verified"])

    def test_host_inventory_never_claims_execution(self):
        result = check_host.check(sorted(check_host.REQUIRED | {"exec_command"}))
        self.assertEqual(result["inventory_check"], "passed")
        self.assertFalse(result["native_execution_verified"])
        self.assertFalse(result["production_reviewer_isolation_verified"])

    def test_bootstrap_env_does_not_forward_credentials(self):
        secrets = {
            "OPENAI_API_KEY": "no", "GITHUB_TOKEN": "no", "GH_TOKEN": "no",
            "ANTHROPIC_API_KEY": "no", "AWS_SECRET_ACCESS_KEY": "no",
            "GIT_SSH_COMMAND": "no", "GIT_CONFIG_COUNT": "1", "PIP_INDEX_URL": "no",
            "PIP_EXTRA_INDEX_URL": "no", "PYTHONPATH": "no", "UNRELATED_SETTING": "no",
            "GIT_SSL_NO_VERIFY": "1", "PIP_TRUSTED_HOST": "untrusted.invalid",
            "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": "no",
        }
        with mock.patch.dict(os.environ, secrets):
            env = bootstrap.clean_env(self.top)
        for key in secrets:
            self.assertNotIn(key, env)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_bootstrap_preserves_exact_network_environment(self):
        network = {name: "fixture-" + name for name in bootstrap.NETWORK_ENV}
        network["HTTPS_PROXY"] = "http://fixture-user:fixture-pass@proxy.invalid:8080"
        with mock.patch.dict(os.environ, network, clear=True):
            env = bootstrap.clean_env(self.top)
        for name, value in network.items():
            self.assertEqual(env[name], value)
        with mock.patch.dict(os.environ, {}, clear=True):
            env = bootstrap.clean_env(self.top)
        self.assertFalse(set(bootstrap.NETWORK_ENV) & set(env))

    def test_bootstrap_space_paths_are_single_arguments(self):
        destination = self.top / "Argus with spaces"
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if "init" in args:
                Path(args[-1]).mkdir()
            stdout = bootstrap.COMMIT if "rev-parse" in args else ""
            return mock.Mock(stdout=stdout)

        with mock.patch.object(bootstrap.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(bootstrap.shutil, "which", return_value="/usr/bin/git"), \
             mock.patch.object(bootstrap, "Installer"), \
             contextlib.redirect_stdout(io.StringIO()):
            result = bootstrap.main(["--target", str(destination), "--allow-network", "--venv", "--install-deps"])
        self.assertEqual(result, 0)
        venv_args = next(args for args, _ in calls if "venv" in args)
        self.assertEqual(venv_args[-1], str(destination / ".venv-dots"))
        pip_args = next(args for args, _ in calls if "pip" in args)
        self.assertEqual(pip_args[0], str(destination / ".venv-dots/bin/python"))
        self.assertEqual(pip_args[-1], str(destination))
        self.assertTrue(all(not kwargs.get("shell") for _, kwargs in calls))

    def test_bootstrap_failure_redacts_network_settings(self):
        proxy = "http://fixture-user:fixture%2Dpassword@proxy.invalid:8080"
        failure = bootstrap.subprocess.CalledProcessError(
            1, ["git", "fetch"], stderr=proxy + " fixture-user fixture-password fixture%2Dpassword /fixture/ca.pem")
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": proxy, "SSL_CERT_FILE": "/fixture/ca.pem"}, clear=True), \
             mock.patch.object(bootstrap.subprocess, "run", side_effect=failure), \
             mock.patch.object(bootstrap.shutil, "which", return_value="/usr/bin/git"), \
             contextlib.redirect_stderr(output):
            result = bootstrap.main(["--target", str(self.top / "new"), "--allow-network"])
        self.assertEqual(result, 2)
        for value in (proxy, "fixture-user", "fixture-password", "fixture%2Dpassword", "/fixture/ca.pem"):
            self.assertNotIn(value, output.getvalue())
        self.assertIn("network setting redacted", output.getvalue())

if __name__ == "__main__":
    unittest.main()
