#!/usr/bin/env python3
"""Optional explicit online bootstrap of the exact official Argus revision.

This does not run automatically from install_dots.py. It needs git and Python
3.11+ on POSIX. --allow-network authorizes this script's fixed public fetch;
--install-deps additionally opts into pip/build execution in a NEW local venv.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from install_dots import InstallError, Installer, no_symlink

REPOSITORY = "https://github.com/lbx154/Argus.git"
COMMIT = "9cfe9129fd90511c3a1865844ec7dfda1b5d1008"


def clean_env(home: Path) -> dict:
    return {
        "PATH": os.defpath,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", required=True, type=Path, help="A new, nonexistent directory; existing directories are never overwritten")
    p.add_argument("--allow-network", action="store_true", help="Explicitly fetch the fixed public source revision from GitHub")
    p.add_argument("--venv", action="store_true", help="Create only target/.venv-dots, without touching global packages")
    p.add_argument("--install-deps", action="store_true", help="Also run isolated pip install -e TARGET inside the new venv; may download/build dependencies")
    a = p.parse_args(argv)
    if not a.allow_network:
        p.error("Online bootstrap requires --allow-network. Existing source installs use install_dots.py offline.")
    if a.install_deps and not a.venv:
        p.error("--install-deps requires --venv")
    if os.name != "posix" or sys.version_info < (3, 11):
        p.error("Requires POSIX and Python 3.11+; native Windows unsupported, WSL unverified")
    target = Path(os.path.abspath(a.target))
    try:
        no_symlink(target)
        if target.exists() or not target.parent.is_dir():
            raise InstallError("Target must not exist and its parent must already be a directory")
        git = shutil.which("git", path=os.defpath)
        if not git:
            raise InstallError("git must be installed in the standard system PATH")
        with tempfile.TemporaryDirectory(prefix=".argus-bootstrap-", dir=target.parent) as temporary:
            temp = Path(temporary)
            home = temp / "home"
            home.mkdir(mode=0o700)
            source = temp / "source"
            env = clean_env(home)
            def run(*args, cwd=None):
                return subprocess.run(args, cwd=cwd, env=env, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()
            opts = (git, "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never", "-c", "init.templateDir=")
            run(*opts, "init", str(source))
            run(*opts, "fetch", "--depth", "1", "--no-tags", REPOSITORY, COMMIT, cwd=source)
            if run(*opts, "rev-parse", "FETCH_HEAD", cwd=source) != COMMIT:
                raise InstallError("Fetched commit differs from the pinned revision")
            run(*opts, "checkout", "--detach", COMMIT, cwd=source)
            installer = Installer(source, Path(__file__).absolute().parent)
            with installer.locked():
                installer.install()
            no_symlink(target)
            if target.exists():
                raise InstallError("Target appeared during bootstrap; refusing overwrite")
            # Same-parent staging means rename is atomic; no pre-existing source is touched.
            source.rename(target)
            if a.venv:
                run(sys.executable, "-m", "venv", str(target / ".venv-dots"))
                if a.install_deps:
                    run(str(target / ".venv-dots/bin/python"), "-m", "pip", "--isolated", "install", "--no-input", "-e", str(target))
        print(json.dumps({"status": "bootstrapped", "source": str(target), "commit": COMMIT, "venv": a.venv, "dependencies_installed": a.install_deps, "native_tools": "not_verified"}))
        return 0
    except (InstallError, OSError, subprocess.CalledProcessError) as e:
        print("bootstrap: " + str(e), file=sys.stderr)
        if isinstance(e, subprocess.CalledProcessError) and e.stderr:
            print(e.stderr[-3000:], file=sys.stderr)
        if target.exists():
            print("Pinned source was created and is retained. An optional venv/setup step may be incomplete; no global configuration was changed.", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
