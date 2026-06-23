from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_DIR = BACKEND_ROOT / "backups" / "mongodb"
DEFAULT_TIMEOUT_SECONDS = 60 * 60 * 3

MONGO_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>mongodb(?:\+srv)?://)(?P<credentials>[^/@\s]+@)",
    re.IGNORECASE,
)


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    return int(value)


def redact_secrets(text: str) -> str:
    return MONGO_CREDENTIALS_RE.sub(r"\g<scheme>***:***@", text)


def log(message: str, log_file: Path) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def ensure_mongodump(mongodump_path: str) -> str:
    resolved = shutil.which(mongodump_path)
    if resolved:
        return resolved

    candidate = Path(mongodump_path)
    if candidate.exists():
        return str(candidate)

    raise FileNotFoundError(
        "mongodump was not found. Install MongoDB Database Tools or set MONGODUMP_PATH."
    )


def has_query_key(query_pairs: list[tuple[str, str]], key: str) -> bool:
    key_lower = key.lower()
    return any(item_key.lower() == key_lower for item_key, _ in query_pairs)


def cluster_uri_from(uri: str, auth_source_from_uri_db: bool = False) -> tuple[str, str | None, bool]:
    """Return a URI that targets the cluster instead of one database.

    If MONGODB_URL contains a database path, mongodump may target only that
    database. For cluster backups we remove the path and optionally preserve it
    as authSource when the deployment authenticates users against that database.
    """
    parts = urlsplit(uri)
    database_name = parts.path.lstrip("/").split("/", 1)[0] if parts.path else ""
    if not database_name:
        return uri, None, False

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    auth_source_added = False
    if auth_source_from_uri_db and not has_query_key(query_pairs, "authSource"):
        query_pairs.append(("authSource", database_name))
        auth_source_added = True

    query = urlencode(query_pairs, doseq=True)
    cluster_uri = urlunsplit((parts.scheme, parts.netloc, "/", query, parts.fragment))
    return cluster_uri, database_name, auth_source_added


def get_backup_uri(
    keep_uri_database: bool,
    auth_source_from_uri_db: bool,
) -> tuple[str, str, str | None, bool]:
    explicit_uri = os.getenv("MONGODB_BACKUP_URI")
    if explicit_uri:
        return explicit_uri, "MONGODB_BACKUP_URI", None, False

    mongo_url = os.getenv("MONGODB_URL")
    if not mongo_url:
        raise RuntimeError("MONGODB_URL is required in the environment or .env file.")

    if keep_uri_database:
        return mongo_url, "MONGODB_URL", None, False

    cluster_uri, removed_db, auth_source_added = cluster_uri_from(
        mongo_url,
        auth_source_from_uri_db=auth_source_from_uri_db,
    )
    return cluster_uri, "MONGODB_URL", removed_db, auth_source_added


class BackupLock:
    def __init__(self, lock_file: Path, stale_after_seconds: int):
        self.lock_file = lock_file
        self.stale_after_seconds = stale_after_seconds
        self.file_descriptor: int | None = None

    def __enter__(self) -> "BackupLock":
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.file_descriptor = os.open(
                self.lock_file,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            age = time.time() - self.lock_file.stat().st_mtime
            if age <= self.stale_after_seconds:
                raise RuntimeError("Another MongoDB backup appears to be running.")

            self.lock_file.unlink()
            self.file_descriptor = os.open(
                self.lock_file,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )

        os.write(self.file_descriptor, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.file_descriptor is not None:
            os.close(self.file_descriptor)
        try:
            self.lock_file.unlink()
        except FileNotFoundError:
            pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_zip_backup(archive_path: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zip_file:
        zip_file.write(archive_path, arcname=archive_path.name)


def delete_previous_backups(backup_dir: Path, current_backup: Path, log_file: Path) -> None:
    current_files = {
        current_backup.resolve(),
        current_backup.with_suffix(current_backup.suffix + ".sha256").resolve(),
    }
    patterns = (
        "mongodb_cluster_*.zip",
        "mongodb_cluster_*.zip.sha256",
        "mongodb_cluster_*.archive.gz",
        "mongodb_cluster_*.archive.gz.sha256",
    )
    removed = 0
    for pattern in patterns:
        for path in backup_dir.glob(pattern):
            if not path.is_file() or path.resolve() in current_files:
                continue

            path.unlink()
            removed += 1

    if removed:
        log(f"Deleted {removed} previous backup file(s).", log_file)


def run_backup(args: argparse.Namespace) -> int:
    backup_dir = Path(args.backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    log_file = backup_dir / "mongodb_backup.log"

    mongodump_path = ensure_mongodump(args.mongodump)
    backup_uri, uri_source, removed_db, auth_source_added = get_backup_uri(
        keep_uri_database=args.keep_uri_database,
        auth_source_from_uri_db=args.auth_source_from_uri_db,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = backup_dir / f"mongodb_cluster_{timestamp}.archive"
    temp_archive_path = backup_dir / f".mongodb_cluster_{timestamp}.archive.tmp"
    zip_path = backup_dir / f"mongodb_cluster_{timestamp}.zip"
    sha_path = zip_path.with_suffix(zip_path.suffix + ".sha256")

    log("Starting MongoDB cluster backup.", log_file)
    log(f"Backup directory: {backup_dir}", log_file)
    log(f"Mongo URI source: {uri_source}", log_file)
    if removed_db:
        log(f"Cluster mode: removed URI database path '{removed_db}'.", log_file)
    if auth_source_added:
        log("Cluster mode: preserved removed database path as authSource.", log_file)

    if args.dry_run:
        log(f"Dry run: would create {zip_path.name}.", log_file)
        log("Dry run: previous backup ZIP/archive files would be deleted after success.", log_file)
        log("Dry run complete; no backup was created.", log_file)
        return 0

    command = [
        mongodump_path,
        f"--uri={backup_uri}",
        f"--archive={temp_archive_path}",
    ]

    completed: subprocess.CompletedProcess[str] | None = None
    elapsed = 0.0
    zip_completed = False
    try:
        with BackupLock(backup_dir / ".mongodb_backup.lock", args.timeout_seconds * 2):
            started_at = time.monotonic()
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=args.timeout_seconds,
                check=False,
            )
            elapsed = time.monotonic() - started_at

            if completed.returncode == 0:
                if not temp_archive_path.exists() or temp_archive_path.stat().st_size == 0:
                    log("Backup failed: mongodump did not create a non-empty archive.", log_file)
                    return 1

                temp_archive_path.replace(archive_path)
                create_zip_backup(archive_path, zip_path)
                zip_completed = True
                archive_path.unlink()
    finally:
        for temp_path in (temp_archive_path, archive_path):
            if temp_path.exists():
                temp_path.unlink()
        if not zip_completed and zip_path.exists():
            zip_path.unlink()

    if completed and completed.stdout:
        log(redact_secrets(completed.stdout.strip()), log_file)
    if completed and completed.stderr:
        log(redact_secrets(completed.stderr.strip()), log_file)

    if completed and completed.returncode != 0:
        if zip_path.exists():
            zip_path.unlink()
        log(f"Backup failed after {elapsed:.1f}s with exit code {completed.returncode}.", log_file)
        return completed.returncode

    if not zip_path.exists() or zip_path.stat().st_size == 0:
        log("Backup failed: ZIP file was not created.", log_file)
        return 1

    digest = sha256_file(zip_path)
    sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="ascii")
    log(
        f"Backup complete: {zip_path.name} ({zip_path.stat().st_size} bytes, {elapsed:.1f}s).",
        log_file,
    )
    log(f"SHA256: {digest}", log_file)
    delete_previous_backups(backup_dir, zip_path, log_file)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a local compressed backup of the MongoDB cluster with mongodump.",
    )
    parser.add_argument(
        "--backup-dir",
        default=os.getenv("MONGODB_BACKUP_DIR", str(DEFAULT_BACKUP_DIR)),
        help="Directory where backup archives and logs are stored.",
    )
    parser.add_argument(
        "--mongodump",
        default=os.getenv("MONGODUMP_PATH", "mongodump"),
        help="Path to mongodump. Defaults to the executable found in PATH.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=parse_int(os.getenv("MONGODB_BACKUP_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS),
        help="Maximum time allowed for one backup run.",
    )
    parser.add_argument(
        "--keep-uri-database",
        action="store_true",
        default=parse_bool(os.getenv("MONGODB_BACKUP_KEEP_URI_DATABASE"), False),
        help="Use MONGODB_URL exactly as-is. This may backup only one database if the URI has a database path.",
    )
    parser.add_argument(
        "--auth-source-from-uri-db",
        action="store_true",
        default=parse_bool(os.getenv("MONGODB_BACKUP_AUTH_SOURCE_FROM_URI_DB"), False),
        help="When deriving a cluster URI, reuse the removed URI database path as authSource.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and show the target file without running mongodump.",
    )
    return parser


def main() -> int:
    load_dotenv(BACKEND_ROOT / ".env")
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    try:
        return run_backup(args)
    except subprocess.TimeoutExpired:
        backup_dir = Path(args.backup_dir).resolve()
        log(f"Backup timed out after {args.timeout_seconds}s.", backup_dir / "mongodb_backup.log")
        return 124
    except Exception as exc:
        backup_dir = Path(args.backup_dir).resolve()
        log(f"Backup failed: {exc}", backup_dir / "mongodb_backup.log")
        return 1


if __name__ == "__main__":
    sys.exit(main())
