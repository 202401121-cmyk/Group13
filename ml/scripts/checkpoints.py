"""Which training checkpoint to resume from (S3-4). A checkpoint counts only once its DONE marker is written."""

from __future__ import annotations

import re
from pathlib import Path

DONE = "DONE"
_NAME = re.compile(r"epoch-(\d{3})")


def checkpoint_dir(root: Path, epoch: int) -> Path:
    return root / f"epoch-{epoch:03d}"


def mark_done(folder: Path) -> None:
    """Written last, so a run stopped halfway through saving leaves a folder that is not resumed from."""
    (folder / DONE).write_text("ok\n", encoding="utf-8")


def latest_checkpoint(root: Path) -> tuple[int, Path] | None:
    """(epoch, folder) of the last finished checkpoint under root, or None."""
    if not root.is_dir():
        return None
    found = [
        (int(match[1]), path)
        for path in root.iterdir()
        if path.is_dir() and (match := _NAME.fullmatch(path.name)) and (path / DONE).is_file()
    ]
    return max(found, default=None)
