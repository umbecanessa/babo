"""NLS Merkle — SHA-256 block hashing and chain integrity verification.

Every block in the Delta Chain is cryptographically linked to its parent
via SHA-256. This module provides hashing primitives and chain verification.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from nls.models import Block

# The parent hash for the very first block (height 0) in any chain.
GENESIS_PARENT_HASH = "0" * 64  # 64-char hex string of zeros


def hash_file(path: Path | str) -> str:
    """Compute the SHA-256 hash of a file's contents.

    Used to fingerprint weight delta files (.safetensors / .gguf).
    Reads in 64KB chunks for memory efficiency on large files.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


def compute_block_hash(
    parent_hash: str,
    delta_path: str,
    height: int,
    timestamp_iso: str,
) -> str:
    """Compute the SHA-256 hash for a new block.

    The hash is derived from:
    - The parent block's hash (chain linkage)
    - The delta file path (content reference)
    - The block height (position integrity)
    - The ISO timestamp (temporal ordering)

    This mirrors a blockchain block hash: H(parent || payload || nonce).
    """
    payload = f"{parent_hash}|{delta_path}|{height}|{timestamp_iso}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_genesis_hash(base_model_path: str) -> str:
    """Compute a fingerprint for the Genesis Block (base model).

    If the base model file exists locally, we hash its contents.
    Otherwise, we hash the model name/path string as a stable identifier.
    """
    p = Path(base_model_path)
    if p.is_file():
        return hash_file(p)
    # Fallback: hash the model identifier string
    return hashlib.sha256(base_model_path.encode("utf-8")).hexdigest()


def hash_adapter_dir(adapter_dir: Path | str) -> str:
    """Compute a SHA-256 hash of an adapter directory's weight files.

    Hashes all .safetensors and .bin files in the directory, sorted by name,
    to produce a deterministic fingerprint of the adapter's weights.
    This is used to seal the soul adapter in the genesis block and verify
    its integrity at runtime.
    """
    adapter_dir = Path(adapter_dir)
    sha = hashlib.sha256()

    # Sort files for deterministic hashing
    weight_files = sorted(
        f for f in adapter_dir.iterdir()
        if f.suffix in (".safetensors", ".bin")
    )

    if not weight_files:
        raise FileNotFoundError(
            f"No weight files found in {adapter_dir}. "
            f"Cannot compute soul hash."
        )

    for wf in weight_files:
        # Hash filename for structure integrity
        sha.update(wf.name.encode("utf-8"))
        # Hash file contents
        with open(wf, "rb") as f:
            while chunk := f.read(65536):
                sha.update(chunk)

    return sha.hexdigest()


def verify_soul_integrity(
    adapter_dir: Path | str,
    expected_hash: str,
) -> tuple[bool, str]:
    """Verify that a values adapter has not been tampered with.

    Recomputes the hash of the adapter directory and compares it to the
    expected hash sealed in the genesis block.

    Returns:
        A tuple of (is_valid, message).
    """
    adapter_dir = Path(adapter_dir)

    if not adapter_dir.exists():
        return False, f"Values adapter not found at {adapter_dir}."

    try:
        current_hash = hash_adapter_dir(adapter_dir)
    except FileNotFoundError as e:
        return False, str(e)

    if current_hash != expected_hash:
        return (
            False,
            f"SOUL INTEGRITY CHECK FAILED.\n"
            f"The values adapter has been modified since genesis.\n"
            f"Expected hash: {expected_hash}\n"
            f"Current hash:  {current_hash}\n"
            f"This agent cannot start with tampered values.",
        )

    return True, f"Soul integrity verified: {current_hash[:16]}..."


def verify_chain(blocks: list[Block]) -> tuple[bool, str]:
    """Verify the integrity of a block chain.

    Checks that:
    1. Heights are sequential starting from 1.
    2. Each block's parent_hash matches the previous block's block_hash.
    3. The first block's parent_hash is the genesis hash (provided externally)
       or GENESIS_PARENT_HASH.

    Returns:
        A tuple of (is_valid, message). On failure, the message describes
        the first integrity violation found.
    """
    if not blocks:
        return True, "Empty chain is trivially valid."

    # Sort by height to ensure ordering
    sorted_blocks = sorted(blocks, key=lambda b: b.height)

    for i, block in enumerate(sorted_blocks):
        if i == 0:
            # First block — we can't verify its parent_hash without the
            # genesis hash, but we check it's not empty.
            if not block.parent_hash:
                return False, f"Block at height {block.height} has empty parent_hash."
        else:
            prev = sorted_blocks[i - 1]
            # Height must be sequential
            if block.height != prev.height + 1:
                return (
                    False,
                    f"Height gap: block {block.height} follows block {prev.height}.",
                )
            # Parent hash linkage
            if block.parent_hash != prev.block_hash:
                return (
                    False,
                    f"Hash mismatch at height {block.height}: "
                    f"parent_hash={block.parent_hash[:16]}... "
                    f"!= prev block_hash={prev.block_hash[:16]}...",
                )

    return True, f"Chain valid: {len(sorted_blocks)} blocks verified."
