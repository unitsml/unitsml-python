"""Package the pinned native UnitsML build in a host-platform wheel."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def validated_receipt(path, pins, binary, metadata_path, platform_tag):
    """CI-produced artifact path: every receipt field must agree with the pins, the host
    platform and the actual files. Nothing is skipped or rewritten."""
    receipt = json.loads(Path(path).read_bytes())
    if receipt.get("kind") != "native-build-receipt/v1":
        raise RuntimeError("Build receipt has an unknown kind")
    if receipt["native_source_commit"] != pins["native_source_commit"]:
        raise RuntimeError("Build receipt source commit differs from the pinned native source")
    if receipt["cargo_lock_sha256"] != pins["cargo_lock_sha256"]:
        raise RuntimeError("Build receipt Cargo.lock differs from the pinned lockfile")
    if receipt["platform_tag"] != pins["platform_tag"] or receipt["platform_tag"] != platform_tag:
        raise RuntimeError("Build receipt platform differs from the pinned wheel platform")
    if receipt["library"] != binary.name:
        raise RuntimeError("Build receipt names a different library")
    if digest(binary) != receipt["library_sha256"]:
        raise RuntimeError("Library differs from the binary recorded in the build receipt")
    if digest(metadata_path) != receipt["cargo_metadata_sha256"]:
        raise RuntimeError("Cargo metadata differs from the build receipt")
    return receipt


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--rust-source", required=True, type=Path)
    cli.add_argument("--library", required=True, type=Path)
    cli.add_argument("--cargo-metadata", required=True, type=Path)
    cli.add_argument("--wheel-dir", required=True, type=Path)
    cli.add_argument("--build-receipt", type=Path, help="CI build receipt from scripts/build-native-receipt.py; replaces only the fixed library_sha256 comparison, after validating the receipt")
    args = cli.parse_args()
    source = args.rust_source.resolve()
    pins = json.loads((ROOT / "native-source.json").read_bytes())
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if revision != pins["native_source_commit"]:
        raise RuntimeError("Rust checkout is at a different revision")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=source):
        raise RuntimeError("Rust checkout must be clean")
    if digest(source / "Cargo.lock") != pins["cargo_lock_sha256"]:
        raise RuntimeError("Cargo.lock differs from the pinned bytes")
    binary = args.library.resolve()
    platform_tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    if platform_tag != pins["platform_tag"] or binary.name != "libunitsml_rs.so":
        raise RuntimeError("Native library does not target this wheel platform")
    build_receipt = None
    if args.build_receipt:
        build_receipt = validated_receipt(args.build_receipt, pins, binary, args.cargo_metadata, platform_tag)
    elif digest(binary) != pins["library_sha256"]:
        raise RuntimeError("Library differs from the pinned native build")
    metadata = json.loads(args.cargo_metadata.read_bytes())
    if Path(metadata["workspace_root"]).resolve() != source:
        raise RuntimeError("Cargo metadata belongs to a different checkout")
    env = os.environ.copy()
    with TemporaryDirectory(prefix="unitsml-wheel-") as temporary:
        staging = Path(temporary)
        notices = staging / "licenses"
        notices.mkdir()
        dependencies = []
        for package in metadata["packages"]:
            base = Path(package["manifest_path"]).parent
            name_version = f"{package['name']}-{package['version']}"
            files = {path for path in base.iterdir() if path.is_file() and
                     path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))}
            if package.get("license_file"):
                files.add(base / package["license_file"])
            recorded = {}
            for path in sorted(files):
                destination = notices / name_version / path.name
                destination.parent.mkdir(exist_ok=True)
                shutil.copyfile(path, destination)
                recorded[str(destination.relative_to(notices))] = digest(destination)
            dependencies.append({
                "name": package["name"], "version": package["version"],
                "license": package.get("license"), "notices": recorded,
            })
        (notices / "manifest.json").write_text(json.dumps(dependencies, indent=2) + "\n")
        receipt = staging / "build-info.json"
        receipt.write_text(json.dumps({
            **pins, "library": binary.name, "sha256": digest(binary), "abi_version": 1,
            "binary_provenance": "ci-build-receipt" if build_receipt else "pinned-binary",
            "build_receipt": build_receipt,
            "cargo_metadata_sha256": digest(args.cargo_metadata),
        }, indent=2) + "\n")
        env["UNITSML_NATIVE"] = str(binary)
        env["UNITSML_NATIVE_RECEIPT"] = str(receipt)
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-build-isolation", "--no-deps",
             "--wheel-dir", str(args.wheel_dir.resolve()), "."],
            cwd=ROOT, env=env, check=True,
        )


if __name__ == "__main__":
    main()
