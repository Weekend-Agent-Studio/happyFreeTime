"""Archive a STOPPED application's data volume; no live SQLite file copying."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
import tarfile
import uuid


def archive_volume(source: Path, destination: Path) -> Path:
    if not source.is_dir():
        raise ValueError("data volume directory is missing")
    source = source.resolve()
    destination = destination.resolve()
    if destination == source or source in destination.parents:
        raise ValueError("backup destination must be outside the source volume")
    os.umask(0o077)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = destination / f"hft-{stamp}-{uuid.uuid4().hex[:8]}.tar.gz"
    try:
        with tarfile.open(archive, "x:gz") as output:
            output.add(source, arcname="data")
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/source"))
    parser.add_argument("--output-dir", type=Path, default=Path("/backups"))
    args = parser.parse_args()
    result = archive_volume(args.source, args.output_dir)
    print(f"Private maintenance snapshot created: {result.name}")


if __name__ == "__main__":
    main()
