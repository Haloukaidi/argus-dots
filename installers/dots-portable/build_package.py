#!/usr/bin/env python3
"""Maintainer-only local packager. Never run against an unreviewed payload."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

BASE = "9cfe9129fd90511c3a1865844ec7dfda1b5d1008"
p = argparse.ArgumentParser()
p.add_argument("--base-repo", type=Path, required=True)
p.add_argument("--payload-dir", type=Path, required=True)
p.add_argument("--legacy-dir", type=Path, help="Initial legacy snapshot; omit when retaining an exact previous-package legacy map")
p.add_argument("--release", default="dots-portable-2.3.4")
p.add_argument("--previous-package", type=Path, help="Directory containing the exact previously delivered manifest.json")
a = p.parse_args()
previous = json.loads((a.previous_package / "manifest.json").read_text()) if a.previous_package else None
if previous and previous["base_commit"] != BASE:
    raise SystemExit("Previous package uses a different base")
if not a.legacy_dir and previous is None:
    p.error("An initial build needs --legacy-dir; upgrades may retain the previous package's exact legacy map")
root = Path(__file__).resolve().parent
files = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE], cwd=a.base_repo, text=True).splitlines()
def digest(data):
    return hashlib.sha256(data).hexdigest()
base = {}
payload_paths = {f.relative_to(a.payload_dir).as_posix() for f in a.payload_dir.rglob("*") if f.is_file()}
for name in files:
    if ((name.startswith(("argus/", "argus_skill/")) and name.endswith(".py")) or name in ("argus_doctor.py", "pyproject.toml") or name.startswith("packages/contracts/schemas/") or name in payload_paths):
        base[name] = digest(subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=a.base_repo))
payload = {}
for source in sorted(a.payload_dir.rglob("*")):
    if source.is_file():
        rel = source.relative_to(a.payload_dir).as_posix()
        target = root / "payload" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)
        payload[rel] = {"sha256": digest(source.read_bytes()), "mode": 0o644}
legacy = ({f.relative_to(a.legacy_dir).as_posix(): digest(f.read_bytes()) for f in sorted(a.legacy_dir.rglob("*")) if f.is_file()}
          if a.legacy_dir else dict(previous.get("legacy", {})))
manifest = {"schema": 1, "release": a.release, "base_commit": BASE, "base_repository": "https://github.com/lbx154/Argus", "base": base, "payload": payload, "legacy": legacy, "upgrade_from": []}
if previous:
    # Preserve earlier exact-source migration identities, then add the direct
    # predecessor. A test-only payload change still requires a new release.
    upgrades = dict(previous.get("upgrade_payloads", {}))
    upgrades[previous["release"]] = {r: i["sha256"] for r, i in previous["payload"].items()}
    manifest["upgrade_from"] = sorted(upgrades)
    manifest["upgrade_payloads"] = upgrades
(root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
print(json.dumps({"baseline_files": len(base), "payload_files": len(payload), "legacy_files": len(legacy)}))
