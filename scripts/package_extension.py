#!/usr/bin/env python3
"""Build deterministic, installer-compatible Neo4j target archives (stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

EXTENSION_ID = "flowsteward.neo4j"
VERSION = "1.0.16"
TOP_FOLDER = "flowsteward_neo4j"
SOURCE_DATE_EPOCH = 1793908560
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 150 * 1024 * 1024
MACHINES = {"linux-amd64": 62, "linux-arm64": 183}
PAYLOAD_DIRECTORIES = frozenset({"contracts", "docs", "scripts", "tests", "ui"})
PAYLOAD_FILES = frozenset(
    {
        "BRAND-ASSETS.md",
        "CHANGELOG.md",
        "LICENSE",
        "NOTICE",
        "README.md",
        "commands.py",
        "extension-descriptor.yaml",
        "extension.yaml",
        "health.py",
        "icon.png",
        "main.py",
        "pyproject.toml",
        "requirements-dev.txt",
        "toolbox.py",
        "validation.py",
    }
)
GENERATED_METADATA = frozenset(
    {
        Path("toolbox-provenance.json"),
        Path("SHA256SUMS"),
        Path("neo4j-driver-provenance.json"),
        Path("scripts/go-toolchain-selected.json"),
    }
)
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        ".fs-python",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "dev-wheels",
        "dist",
        "logs",
        ".cache",
    }
)
CONTROL_FILES = frozenset({".gitattributes", ".gitignore"})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 64), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot(root: Path) -> tuple[dict[Path, tuple[bytes, int]], set[Path]]:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root)
        if Path(os.fsdecode(top).rstrip("\n")).resolve() != root:
            raise ValueError("Packaging requires the root of a Git checkout")
        source = subprocess.check_output(["git", "archive", "--format=tar", "HEAD"], cwd=root)
    except subprocess.CalledProcessError as exc:
        raise ValueError("Packaging requires a committed Git source tree") from exc
    payload = {}
    tracked = set()
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:") as archive:
        for member in archive:
            path = Path(member.name)
            tracked.add(path)
            if member.isdir() or path.parts[0] == ".github" or member.name in CONTROL_FILES:
                continue
            if not member.isfile() or (
                path.parts[0] not in PAYLOAD_DIRECTORIES and member.name not in PAYLOAD_FILES
            ):
                raise ValueError(f"Unexpected package input: {path}")
            with archive.extractfile(member) as content:
                payload[path] = (content.read(), 0o755 if member.mode & 0o111 else 0o644)
    return payload, tracked


def _check_workspace(root: Path, out_dir: Path, approved: set[Path]) -> None:
    approved = approved | {parent for path in approved for parent in path.parents}
    for directory, directories, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in (*directories, *files):
            path = base / name
            relative = path.relative_to(root)
            if path.is_symlink():
                raise ValueError(f"Unexpected package input: {relative}")
            if name in directories and (name in EXCLUDED_DIRECTORIES or path == out_dir):
                directories.remove(name)
                continue
            if relative == Path(".git") or (name in directories and path in out_dir.parents):
                continue
            if relative not in approved or (name in files and not path.is_file()):
                raise ValueError(f"Unexpected package input: {relative}")


def _native_metadata(root: Path, target: str) -> dict[str, str]:
    binary = root / "bin" / target / "toolbox"
    if any(path.is_symlink() for path in (root / "bin", binary.parent, binary)):
        raise ValueError("Native executable and parent directories must not be symlinks")
    if not binary.is_file() or stat.S_IMODE(binary.stat().st_mode) != 0o755:
        raise ValueError("Native executable must exist with mode 0755")
    with binary.open("rb") as stream:
        header = stream.read(20)
    if len(header) != 20 or header[:5] != b"\x7fELF\x02" or header[5] not in (1, 2):
        raise ValueError("Native executable must be a valid ELF64 binary")
    machine = (
        int.from_bytes(header[18:20], "little")
        if header[5] == 1
        else int.from_bytes(header[18:20], "big")
    )
    if machine != MACHINES[target]:
        raise ValueError("Native executable ELF machine does not match runtime target")
    provenance_path = root / "toolbox-provenance.json"
    if provenance_path.is_symlink():
        raise ValueError("Provenance must not be a symlink")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    record = provenance["binaries"][target]
    digest = sha256(binary)
    if record.get("sha256") != digest or record.get("size") != binary.stat().st_size:
        raise ValueError("Native executable SHA-256 or size does not match provenance")
    # Read the actual ELF build information; truthful old provenance cannot bypass the floor.
    helper_path = Path(__file__).resolve().with_name("native_build_info.py")
    spec = importlib.util.spec_from_file_location("native_security_preflight", helper_path)
    if spec is None or spec.loader is None:
        raise ValueError("Native security preflight is unavailable")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.verify_security(root, target, provenance)
    return {"path": f"bin/{target}/toolbox", "sha256": digest}


def _write_entry(archive: zipfile.ZipFile, relative: str, data: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(f"{TOP_FOLDER}/{relative}", time.gmtime(SOURCE_DATE_EPOCH)[:6])
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data, compresslevel=9)


def package_extension(root: Path, target: str, out_dir: Path) -> Path:
    """Verify a native payload, then atomically publish one target-specific ZIP."""
    if target not in MACHINES:
        raise ValueError("Unsupported runtime target")
    root = root.resolve(strict=True)
    out_dir = out_dir.resolve()
    if out_dir == root:
        raise ValueError("Output directory must differ from the extension root")
    if out_dir.is_relative_to(root) and out_dir.relative_to(root).parts[
        0
    ] in PAYLOAD_DIRECTORIES | {"bin"}:
        raise ValueError("Output directory must not overlap release payload directories")
    payload, tracked = _snapshot(root)
    native_paths = {Path("bin") / runtime / "toolbox" for runtime in MACHINES}
    _check_workspace(root, out_dir, tracked | GENERATED_METADATA | native_paths)
    native = _native_metadata(root, target)
    for relative in GENERATED_METADATA | {Path(native["path"])}:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"Required release file missing: {relative}")
        if path.stat().st_size > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Extension archive exceeds installer uncompressed-size limit")
        payload[relative] = (
            path.read_bytes(),
            0o755 if relative.as_posix() == native["path"] else 0o644,
        )
    for required in (
        "LICENSE",
        "NOTICE",
        "toolbox-provenance.json",
        "SHA256SUMS",
        "neo4j-driver-provenance.json",
        "scripts/go-toolchain-selected.json",
        "extension.yaml",
        "extension-descriptor.yaml",
    ):
        if Path(required) not in payload:
            raise ValueError(f"Required release file missing: {required}")
    metadata = (
        json.dumps({"runtime_target": target, "native_executable": native}, sort_keys=True).encode()
        + b"\n"
    )
    if sum(len(data) for data, _ in payload.values()) + len(metadata) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("Extension archive exceeds installer uncompressed-size limit")
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"{EXTENSION_ID}-{VERSION}-{target}.zip"
    descriptor, temporary = tempfile.mkstemp(prefix=".package-", suffix=".zip", dir=out_dir)
    os.close(descriptor)
    pending = Path(temporary)
    try:
        with zipfile.ZipFile(pending, "w") as archive:
            for path, (data, mode) in sorted(payload.items()):
                relative = path.as_posix()
                if data.startswith(b"\x7fELF") and relative != native["path"]:
                    raise ValueError("Package must contain exactly one native executable")
                if (
                    relative == native["path"]
                    and hashlib.sha256(data).hexdigest() != native["sha256"]
                ):
                    raise ValueError("Native executable changed during packaging")
                _write_entry(archive, relative, data, mode)
            _write_entry(archive, ".fs-package.yaml", metadata, 0o644)
        if pending.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("Extension archive exceeds installer compressed-size limit")
        with zipfile.ZipFile(pending) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Extension archive exceeds installer uncompressed-size limit")
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    return destination


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-target", required=True, choices=sorted(MACHINES))
    parser.add_argument("--out-dir", type=Path, default=root / "dist")
    args = parser.parse_args()
    try:
        archive = package_extension(root, args.runtime_target, args.out_dir)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f"Packaging failed: {error}\n")
    print(f"[PASS] packed: {archive}")
    print(f"[PASS] sha256: {sha256(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
