"""Seal/recover private PR249 artifacts with the existing private backup binding.

Only ciphertext, a random salt/nonce, authentication tag and checksums are public.
Keep the original PR249_RENDER_EXPORT_URL value in its existing private vault;
its download expiry does not affect recovery. Never publish an encryption key.
Requires cryptography; does not call providers, auth routes, Docker or the live DB.
"""

import argparse
import hashlib
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PRIVATE = Path("/workspace/AlphaTrade-AI/.ai/local/pr249-release")
CONTEXT = b"AlphaTrade PR249 private recovery v1:60a5550"
FILES = [
    "replacement/a13-replacement-images.tar.gz",
    "replacement/qualified-manifest.json",
    "replacement/replacement-source-manifest.json",
    "replacement/archive-source-identity.json",
    "synthetic-auth.json",
    "hosted-backup/render-export.dir.tar.gz",
    "hosted-backup/download-receipt.json",
    "hosted-backup/restore-receipt.json",
]


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def derive(salt):
    secret = os.environ.get("PR249_RENDER_EXPORT_URL", "").strip()
    if not secret or len(secret) < 64:
        raise RuntimeError("existing_private_backup_binding_required")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=CONTEXT).derive(
        secret.encode()
    )


def seal(destination):
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "manifest.json").exists():
        raise RuntimeError("sealed_artifact_exists_reuse_it")
    salt, nonce = os.urandom(32), os.urandom(12)
    encryptor = Cipher(algorithms.AES(derive(salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(CONTEXT)
    with tempfile.TemporaryDirectory(dir=PRIVATE) as work:
        bundle = Path(work) / "private.tar"
        with tarfile.open(bundle, "w") as archive:
            for name in FILES:
                source = PRIVATE / name
                if (
                    source.is_symlink()
                    or not source.is_file()
                    or source.stat().st_mode & 0o077
                ):
                    raise RuntimeError("private_regular_artifact_required")
                archive.add(source, arcname=name, recursive=False)
        parts = []
        with bundle.open("rb") as source:
            index = 0
            while block := source.read(32 * 1024**2):
                name = f"part-{index:03d}.bin"
                target = destination / name
                target.write_bytes(encryptor.update(block))
                parts.append(
                    {
                        "name": name,
                        "size": target.stat().st_size,
                        "sha256": digest(target),
                    }
                )
                index += 1
        assert encryptor.finalize() == b""
        metadata = {
            "version": 1,
            "algorithm": "AES-256-GCM/HKDF-SHA256",
            "salt_hex": salt.hex(),
            "nonce_hex": nonce.hex(),
            "tag_hex": encryptor.tag.hex(),
            "parts": parts,
            "plaintext_bundle_sha256": digest(bundle),
            "recovery_binding": "original private PR249_RENDER_EXPORT_URL value; never log",
            "files": FILES,
            "key_published": False,
        }
        (destination / "manifest.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )
    print(
        json.dumps(
            {"sealed": True, "ciphertext_parts": len(parts), "key_published": False}
        )
    )


def recover(source):
    metadata = json.loads((source / "manifest.json").read_text())
    if metadata["version"] != 1 or metadata["files"] != FILES:
        raise RuntimeError("unexpected_recovery_format")
    decryptor = Cipher(
        algorithms.AES(derive(bytes.fromhex(metadata["salt_hex"]))),
        modes.GCM(
            bytes.fromhex(metadata["nonce_hex"]), bytes.fromhex(metadata["tag_hex"])
        ),
    ).decryptor()
    decryptor.authenticate_additional_data(CONTEXT)
    with tempfile.TemporaryDirectory(dir=PRIVATE) as work:
        bundle = Path(work) / "verified-private.tar"
        with bundle.open("xb") as output:
            for part in metadata["parts"]:
                if not re.fullmatch(r"part-[0-9]{3}\.bin", part["name"]):
                    raise RuntimeError("unexpected_ciphertext_part")
                path = source / part["name"]
                if (
                    path.is_symlink()
                    or path.stat().st_size != part["size"]
                    or digest(path) != part["sha256"]
                ):
                    raise RuntimeError("ciphertext_identity_mismatch")
                with path.open("rb") as chunk:
                    while block := chunk.read(1024**2):
                        output.write(decryptor.update(block))
            output.write(decryptor.finalize())
        if digest(bundle) != metadata["plaintext_bundle_sha256"]:
            raise RuntimeError("private_bundle_identity_mismatch")
        seen = set()
        with tarfile.open(bundle) as archive:
            for member in archive:
                if (
                    member.name not in FILES
                    or member.name in seen
                    or not member.isfile()
                ):
                    raise RuntimeError("unexpected_private_archive_member")
                seen.add(member.name)
                target = PRIVATE / member.name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                data = archive.extractfile(member)
                if target.exists():
                    if target.is_symlink() or target.stat().st_mode & 0o077:
                        raise RuntimeError("existing_private_target_invalid")
                    if hashlib.file_digest(data, "sha256").hexdigest() != digest(
                        target
                    ):
                        raise RuntimeError(
                            "existing_private_target_differs_no_overwrite"
                        )
                else:
                    with target.open("xb") as output:
                        while block := data.read(1024**2):
                            output.write(block)
                    target.chmod(0o600)
        if seen != set(FILES):
            raise RuntimeError("private_bundle_incomplete")
    print(
        json.dumps(
            {
                "recovery_verified": True,
                "private_files": len(seen),
                "existing_private_files_overwritten": False,
                "secret_values_printed": False,
            }
        )
    )


if __name__ == "__main__":
    os.umask(0o077)
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["seal", "recover"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    (seal if args.mode == "seal" else recover)(args.directory)
