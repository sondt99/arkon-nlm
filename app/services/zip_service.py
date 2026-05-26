"""
Zip extraction service — in-memory, security-hardened.

Security measures
-----------------
1. ZipSlip defence: every entry path is reduced to its basename via
   os.path.basename(). Directory components (including ../ traversal
   and absolute paths like /etc/passwd) are discarded entirely before
   the name is used anywhere in the application.
2. Zip-bomb protection: the declared uncompressed size (from the zip
   central directory) is checked BEFORE reading. After decompression
   the actual byte count is verified again as a secondary guard.
3. Entry count cap (MAX_ENTRIES): prevents archives with thousands of
   tiny files from exhausting DB connections or worker queue capacity.
4. Per-file size cap (MAX_SINGLE_BYTES): prevents a single huge entry
   from exhausting memory.
5. Total uncompressed size cap (MAX_TOTAL_BYTES): aggregate guard.
6. Allowlist by extension: only document types that the ingestion
   pipeline supports are extracted; all others are recorded as skipped.
7. Metadata artefacts: macOS __MACOSX directories, hidden dot-files,
   and common OS junk files (Thumbs.db, desktop.ini) are discarded.
8. Corrupted data: zipfile.BadZipFile and zipfile.LargeZipFile are
   caught and re-raised as ZipExtractionError with a clear message.
"""

import io
import os
import zipfile
from dataclasses import dataclass, field

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".doc", ".txt", ".md",
    ".csv", ".xlsx", ".pptx",
})

MAX_ENTRIES      = 50
MAX_SINGLE_BYTES = 50 * 1024 * 1024    # 50 MB per extracted file
MAX_TOTAL_BYTES  = 500 * 1024 * 1024   # 500 MB total uncompressed


class ZipExtractionError(Exception):
    """Raised for invalid, corrupted, or policy-violating zip archives."""


@dataclass
class ExtractedFile:
    filename: str   # safe basename — no path components
    data: bytes


@dataclass
class ZipResult:
    files: list[ExtractedFile] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # original names + reason


def extract_zip(data: bytes) -> ZipResult:
    """
    Extract a zip archive from raw bytes.

    Returns a ZipResult whose `.files` contains only supported document
    files with sanitised basenames. Unsupported or oversized entries are
    recorded in `.skipped` instead of raising an error, so the caller
    can still process the valid subset.

    Raises ZipExtractionError for:
    - corrupted / non-zip data
    - entry count exceeding MAX_ENTRIES
    - aggregate uncompressed size exceeding MAX_TOTAL_BYTES
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ZipExtractionError("Invalid or corrupted zip file.")
    except zipfile.LargeZipFile:
        raise ZipExtractionError("Zip file requires ZIP64 extensions and is too large.")

    result = ZipResult()
    total_bytes = 0

    with zf:
        # Filter out directory entries up front
        file_infos = [i for i in zf.infolist() if not i.is_dir()]

        if len(file_infos) > MAX_ENTRIES:
            raise ZipExtractionError(
                f"Archive contains {len(file_infos)} files; maximum allowed is {MAX_ENTRIES}."
            )

        for info in file_infos:
            safe_name = _safe_filename(info.filename)
            if safe_name is None:
                result.skipped.append(info.filename)
                continue

            # Primary zip-bomb guard: check declared size before allocating memory
            if info.file_size > MAX_SINGLE_BYTES:
                result.skipped.append(
                    f"{info.filename} "
                    f"(declared {info.file_size // 1_048_576} MB exceeds 50 MB limit)"
                )
                continue

            total_bytes += info.file_size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ZipExtractionError(
                    f"Total uncompressed size exceeds {MAX_TOTAL_BYTES // 1_048_576} MB limit."
                )

            file_data = zf.read(info.filename)

            # Secondary zip-bomb guard: verify actual decompressed size
            if len(file_data) > MAX_SINGLE_BYTES:
                result.skipped.append(
                    f"{info.filename} "
                    f"(actual {len(file_data) // 1_048_576} MB exceeds 50 MB limit)"
                )
                continue

            result.files.append(ExtractedFile(filename=safe_name, data=file_data))

    return result


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
