"""
Zip extraction service — spooled to disk, security-hardened.

Security measures
-----------------
1. ZipSlip defence: every entry path is reduced to its basename via
   os.path.basename(). Directory components (including ../ traversal
   and absolute paths like /etc/passwd) are discarded entirely before
   the name is used anywhere in the application.
2. Zip-bomb protection: the declared uncompressed size (from the zip
   central directory) is checked BEFORE reading, and the inflated bytes
   are counted as they stream, so extraction stops at the cap instead of
   after the whole entry has been inflated.
3. Entry count cap (settings.max_zip_entries): prevents archives with
   thousands of tiny files from exhausting DB connections or worker
   queue capacity.
4. Per-file size cap (settings.max_zip_member_mb): prevents a single
   huge entry from exhausting memory.
5. Total uncompressed size cap (settings.max_zip_total_mb): aggregate guard.
6. Allowlist by extension: only document types that the ingestion
   pipeline supports are extracted; all others are recorded as skipped.
7. Metadata artefacts: macOS __MACOSX directories, hidden dot-files,
   and common OS junk files (Thumbs.db, desktop.ini) are discarded.
8. Corrupted data: zipfile.BadZipFile and zipfile.LargeZipFile are
   caught and re-raised as ZipExtractionError with a clear message.

Memory
------
Entries are inflated in fixed chunks onto one SpooledTemporaryFile that rolls over to
disk, and `ExtractedFile.data` reads its slice back on demand. Previously every entry was
`zf.read()` into a list, so the aggregate cap *was* the resident-memory cost: ten zeroed
50 MB files compress to well under 1 MB on the wire, so one small request drove ~500 MB
RSS and two concurrent requests OOM-killed the API container. Nothing router-side could
reduce that peak — it happened inside extract_zip before the router saw anything.
"""

import io
import os
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import IO, Optional

from app.config import settings

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".doc", ".txt", ".md",
    ".csv", ".xlsx", ".pptx",
})

_INFLATE_CHUNK = 256 * 1024

# Matches Starlette's multipart threshold: small archives stay entirely on the heap and
# cost no temp file, anything larger spills to disk.
_SPOOL_MAX_BYTES = 1024 * 1024

# zipfile signals damaged, truncated, encrypted and unsupported-compression entries with
# this spread of exception types, several of which are not ZipFile-specific. Every one of
# them is caused by the bytes the client sent, so all of them have to become a 4xx —
# `zf.read()` used to let BadZipFile escape as an unhandled 500.
_CORRUPT_MEMBER_ERRORS = (
    zipfile.BadZipFile,    # bad CRC-32, bad local header
    zipfile.LargeZipFile,
    zlib.error,            # corrupt deflate stream — zipfile does not wrap this
    EOFError,              # "Compressed file ended before the end-of-stream marker"
    RuntimeError,          # "File ... is encrypted, password required for extraction"
    NotImplementedError,   # "compression type N (...)"
)


class ZipExtractionError(Exception):
    """Raised for invalid, corrupted, or policy-violating zip archives."""


class ExtractedFile:
    """One archive member, addressed by offset on the shared spool.

    `data` re-reads its slice on every access rather than caching it. Caching would put
    the whole decompressed archive back on the heap as the caller iterated `files`, which
    is precisely the peak this class exists to remove.
    """

    __slots__ = ("filename", "size", "_spool", "_offset")

    def __init__(self, filename: str, size: int, spool: IO[bytes], offset: int) -> None:
        self.filename = filename   # safe basename — no path components
        self.size = size
        self._spool = spool
        self._offset = offset

    @property
    def data(self) -> bytes:
        self._spool.seek(self._offset)
        return self._spool.read(self.size)

    def __repr__(self) -> str:
        return f"ExtractedFile(filename={self.filename!r}, size={self.size})"


@dataclass
class ZipResult:
    files: list[ExtractedFile] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # original names + reason
    spool: Optional[IO[bytes]] = field(default=None, repr=False)

    def close(self) -> None:
        """Release the spool. `data` is unreadable afterwards.

        Optional: the spool is unlinked as soon as it is garbage collected, so a caller
        that simply drops the result leaks nothing. Callers that hold a result for a long
        time should close it to return the disk immediately.
        """
        if self.spool is not None:
            self.spool.close()
            self.spool = None

    def __enter__(self) -> "ZipResult":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def extract_zip(data: bytes) -> ZipResult:
    """
    Extract a zip archive from raw bytes.

    Returns a ZipResult whose `.files` contains only supported document
    files with sanitised basenames. Unsupported or oversized entries are
    recorded in `.skipped` instead of raising an error, so the caller
    can still process the valid subset.

    Raises ZipExtractionError for:
    - corrupted / non-zip data, including a member that fails its CRC
    - entry count exceeding settings.max_zip_entries
    - aggregate uncompressed size exceeding settings.max_zip_total_mb
    """
    max_entries = settings.max_zip_entries
    member_limit = settings.max_zip_member_mb * 1024 * 1024
    total_limit = settings.max_zip_total_mb * 1024 * 1024

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ZipExtractionError("Invalid or corrupted zip file.")
    except zipfile.LargeZipFile:
        raise ZipExtractionError("Zip file requires ZIP64 extensions and is too large.")

    spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES, mode="w+b")
    result = ZipResult(spool=spool)
    total_bytes = 0

    try:
        with zf:
            # Filter out directory entries up front
            file_infos = [i for i in zf.infolist() if not i.is_dir()]

            if len(file_infos) > max_entries:
                raise ZipExtractionError(
                    f"Archive contains {len(file_infos)} files; maximum allowed is {max_entries}."
                )

            for info in file_infos:
                safe_name = _safe_filename(info.filename)
                if safe_name is None:
                    result.skipped.append(info.filename)
                    continue

                # Primary zip-bomb guard: check declared size before inflating anything
                if info.file_size > member_limit:
                    result.skipped.append(
                        f"{info.filename} (declared {info.file_size // 1_048_576} MB "
                        f"exceeds {member_limit // 1_048_576} MB limit)"
                    )
                    continue

                spool.seek(0, os.SEEK_END)
                offset = spool.tell()
                # One byte past whichever cap binds first, so the loop can tell "at the
                # limit" from "over it" without inflating the rest of the entry.
                budget = min(member_limit, total_limit - total_bytes)
                written = _spool_member(zf, info, spool, budget)

                # Secondary zip-bomb guard: the caps have to run on the bytes actually
                # written, because that is what the spool and the total budget account for.
                # zipfile clamps output to the declared size today, so reaching this is a
                # sign the declared size lied and the clamp changed.
                if written > member_limit:
                    spool.seek(offset)
                    spool.truncate()
                    result.skipped.append(
                        f"{info.filename} (actual {written // 1_048_576} MB "
                        f"exceeds {member_limit // 1_048_576} MB limit)"
                    )
                    continue

                total_bytes += written
                if total_bytes > total_limit:
                    raise ZipExtractionError(
                        f"Total uncompressed size exceeds {total_limit // 1_048_576} MB limit."
                    )

                result.files.append(
                    ExtractedFile(
                        filename=safe_name, size=written, spool=spool, offset=offset
                    )
                )
    except BaseException:
        # A partially filled spool is unusable and the caller never receives it, so it has
        # to be released here or the temp file survives until GC.
        result.close()
        raise

    return result


def _spool_member(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, spool: IO[bytes], budget: int
) -> int:
    """Inflate one member onto the spool, stopping one byte past `budget`.

    Chunked rather than `zf.read()`: read() returns the whole member as one heap object, so
    every entry cost its full inflated size even though nothing downstream needed it in one
    piece — and the size check could only run once that allocation had already happened.
    Streaming also means an over-budget entry stops mid-inflate rather than being inflated
    in full and then rejected. Returns the bytes written, for the caller to cap.
    """
    written = 0
    try:
        with zf.open(info) as member:
            while written <= budget:
                chunk = member.read(_INFLATE_CHUNK)
                if not chunk:
                    break
                spool.write(chunk)
                written += len(chunk)
    except _CORRUPT_MEMBER_ERRORS as exc:
        raise ZipExtractionError(
            f"Could not read '{info.filename}' from the archive: {exc}"
        ) from exc
    return written


def _safe_filename(raw: str) -> str | None:
    """
    Sanitise a zip entry path to a safe basename.

    Returns None when the entry should be skipped entirely.

    ZipSlip defence: os.path.basename() discards every directory component
    including path traversal sequences (e.g. ../../etc/passwd becomes
    'passwd') and absolute Unix/Windows paths (/etc/passwd, C:\\Windows\\...).
    The normalisation of backslashes before calling basename covers archives
    created on Windows where the separator may be '\\'.
    """
    # Normalise Windows path separators
    normalised = raw.replace("\\", "/")

    # macOS Archive Utility inserts __MACOSX/ metadata alongside every file
    if "__MACOSX" in normalised:
        return None

    # Strip all path components — this is the ZipSlip defence
    basename = os.path.basename(normalised)

    if not basename:
        return None

    # Hidden files and common OS artefacts
    if basename.startswith("."):
        return None
    if basename.lower() in {"thumbs.db", "desktop.ini", "ds_store"}:
        return None

    ext = os.path.splitext(basename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None

    return basename
