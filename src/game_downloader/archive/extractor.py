from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import zlib
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from game_downloader.models import ExtractionLimits, ExtractionResult

logger = logging.getLogger(__name__)
ExtractionProgressCallback = Callable[[int, int], None]
ResumeProgressCallback = Callable[[int, int], None]
StorageProgressCallback = Callable[["StorageEstimate"], None]

_ZIP_COMPRESSION_NAMES = {
    0: "stored",
    8: "deflate",
    9: "deflate64",
    12: "bzip2",
    14: "lzma",
    20: "zstandard-legacy",
    93: "zstandard",
    98: "ppmd",
}


class ArchiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    size: int
    compressed_size: int
    is_directory: bool = False


@dataclass(frozen=True)
class StorageEstimate:
    extracted_size: int
    cache_budget: int
    peak_size: int
    safety_margin: int
    current_workspace_size: int
    free_space: int
    additional_required: int

    @property
    def enough(self) -> bool:
        return self.free_space >= self.additional_required


@dataclass(frozen=True)
class ZipStreamMetadata:
    archive_size: int
    extracted_size: int
    compressed_members_size: int
    file_count: int


class ArchiveExtractor:
    def __init__(self, limits: ExtractionLimits | None = None) -> None:
        self.limits = limits or ExtractionLimits()

    def list_contents(self, archive: Path) -> list[ArchiveMember]:
        kind = _archive_kind(archive)
        logger.info(
            "Inspecting archive path=%s kind=%s archive_size=%d",
            archive,
            kind,
            archive.stat().st_size,
        )
        if kind == "zip":
            with zipfile.ZipFile(archive) as source:
                members = []
                for info in source.infolist():
                    if _zip_is_link(info):
                        raise ArchiveError("The archive contains a symbolic link.")
                    members.append(
                        ArchiveMember(
                            name=info.filename,
                            size=info.file_size,
                            compressed_size=info.compress_size,
                            is_directory=info.is_dir(),
                        )
                    )
        elif kind == "tar":
            with tarfile.open(archive, mode="r:*") as source:
                members = []
                for info in source.getmembers():
                    if info.issym() or info.islnk():
                        raise ArchiveError("The archive contains a link.")
                    if info.isdev() or info.isfifo():
                        raise ArchiveError("The archive contains a special device entry.")
                    if not (info.isfile() or info.isdir()):
                        raise ArchiveError("The archive contains an unsupported entry type.")
                    members.append(
                        ArchiveMember(
                            name=info.name,
                            size=info.size,
                            compressed_size=info.size,
                            is_directory=info.isdir(),
                        )
                    )
        else:
            members = self._list_7zip(archive)
        self._validate_members(members, archive.stat().st_size)
        return members

    def extract(
        self,
        archive: Path,
        destination: Path,
        *,
        overwrite: bool = False,
        progress: ExtractionProgressCallback | None = None,
    ) -> ExtractionResult:
        if destination.exists():
            if not overwrite:
                raise ArchiveError(
                    "The extraction destination already exists. Confirm overwrite explicitly."
                )
            if any(destination.iterdir()):
                raise ArchiveError(
                    "For safety, choose a new or empty extraction destination."
                )
            destination.rmdir()
        members = self.list_contents(archive)
        total_size = sum(item.size for item in members if not item.is_directory)
        file_count = sum(not item.is_directory for item in members)
        longest_member = max(members, key=lambda item: len(item.name), default=None)
        logger.info(
            "Archive preflight completed files=%d total_size=%d longest_member_length=%d "
            "longest_member=%r",
            file_count,
            total_size,
            len(longest_member.name) if longest_member else 0,
            longest_member.name if longest_member else "",
        )
        if progress:
            progress(0, total_size)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.extracting-", dir=destination.parent)
        )
        kind = _archive_kind(archive)
        logger.info(
            "Archive extraction workspace created kind=%s destination=%s temporary=%s "
            "destination_path_length=%d temporary_path_length=%d",
            kind,
            destination,
            temporary,
            len(str(destination)),
            len(str(temporary)),
        )
        try:
            if kind == "zip":
                if self._find_seven_zip() is not None:
                    self._extract_7zip(archive, temporary)
                else:
                    try:
                        self._extract_zip(archive, temporary, progress, total_size)
                    except (FileNotFoundError, NotImplementedError, RuntimeError) as exc:
                        logger.warning(
                            "Python ZIP extraction is unavailable; trying 7-Zip fallback: %s",
                            exc,
                        )
                        shutil.rmtree(temporary, ignore_errors=True)
                        temporary.mkdir(parents=True)
                        self._extract_7zip(archive, temporary)
                logger.info(
                    "ZIP post-extraction path scan skipped; member paths and sizes were "
                    "validated before extraction"
                )
            elif kind == "tar":
                self._extract_tar(archive, temporary, progress, total_size)
            elif kind == "rar":
                self._extract_rar(archive, temporary)
                self._validate_extracted_tree(temporary)
            else:
                self._extract_7zip(archive, temporary)
                self._validate_extracted_tree(temporary)
            if progress:
                progress(total_size, total_size)
            logger.info(
                "Moving extracted archive into final destination source=%s destination=%s",
                temporary,
                destination,
            )
            os.replace(temporary, destination)
        except Exception:
            logger.exception(
                "Archive extraction failed kind=%s archive=%s destination=%s temporary=%s "
                "archive_path_length=%d destination_path_length=%d temporary_path_length=%d",
                kind,
                archive,
                destination,
                temporary,
                len(str(archive)),
                len(str(destination)),
                len(str(temporary)),
            )
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        logger.info(
            "Archive extraction completed archive=%s destination=%s files=%d",
            archive,
            destination,
            sum(not item.is_directory for item in members),
        )
        return ExtractionResult(
            destination=destination,
            file_count=sum(not item.is_directory for item in members),
            total_size=total_size,
        )

    def extract_zip_stream(
        self,
        archive: object,
        destination: Path,
        *,
        archive_size: int,
        progress: ExtractionProgressCallback | None = None,
        resume_workspace: Path | None = None,
        resume_progress: ResumeProgressCallback | None = None,
        working_storage_size: int = 0,
        storage_progress: StorageProgressCallback | None = None,
    ) -> ExtractionResult:
        """Safely extract a seekable remote ZIP without materializing the ZIP on disk."""
        if destination.exists():
            raise ArchiveError("The extraction destination already exists.")
        with zipfile.ZipFile(archive) as source:
            metadata, infos, members = self._inspect_zip_source(source, archive_size)
            total_size = metadata.extracted_size
            if progress:
                progress(0, total_size)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if resume_workspace is None:
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f".{destination.name}.extracting-", dir=destination.parent
                    )
                )
            else:
                temporary = self._prepare_remote_resume_workspace(
                    resume_workspace, infos
                )
                estimate = _storage_estimate(
                    resume_workspace,
                    destination.parent,
                    total_size,
                    working_storage_size,
                )
                logger.info(
                    "Remote ZIP storage preflight extracted_size=%d cache_budget=%d "
                    "peak_size=%d safety_margin=%d current_workspace_size=%d "
                    "free_space=%d additional_required=%d result=%s",
                    estimate.extracted_size,
                    estimate.cache_budget,
                    estimate.peak_size,
                    estimate.safety_margin,
                    estimate.current_workspace_size,
                    estimate.free_space,
                    estimate.additional_required,
                    "enough" if estimate.enough else "insufficient",
                )
                if storage_progress:
                    storage_progress(estimate)
                if not estimate.enough:
                    raise ArchiveError(
                        "ZIP açıldığında "
                        f"{_format_size(estimate.extracted_size)} olacak; on-demand için "
                        f"yaklaşık {_format_size(estimate.additional_required)} ek boş alan "
                        f"gerekiyor, ancak {_format_size(estimate.free_space)} kullanılabilir."
                    )
            try:
                self._extract_zip_source(
                    source,
                    temporary,
                    progress,
                    total_size,
                    resume=resume_workspace is not None,
                    resume_progress=resume_progress,
                )
                if progress:
                    progress(total_size, total_size)
                os.replace(temporary, destination)
            except Exception:
                if resume_workspace is None:
                    shutil.rmtree(temporary, ignore_errors=True)
                else:
                    logger.info(
                        "Remote ZIP resume workspace preserved path=%s", resume_workspace
                    )
                raise
        return ExtractionResult(
            destination=destination,
            file_count=sum(not item.is_directory for item in members),
            total_size=total_size,
        )

    def inspect_zip_stream(
        self,
        archive: object,
        *,
        archive_size: int,
        validate_limits: bool = True,
    ) -> ZipStreamMetadata:
        """Read and validate only a remote ZIP central directory."""
        with zipfile.ZipFile(archive) as source:
            metadata, _infos, _members = self._inspect_zip_source(
                source, archive_size, validate_limits=validate_limits
            )
        return metadata

    def _inspect_zip_source(
        self,
        source: zipfile.ZipFile,
        archive_size: int,
        *,
        validate_limits: bool = True,
    ) -> tuple[ZipStreamMetadata, list[zipfile.ZipInfo], list[ArchiveMember]]:
        infos = source.infolist()
        methods = Counter(info.compress_type for info in infos if not info.is_dir())
        method_summary = _zip_compression_summary(methods)
        logger.info(
            "Remote ZIP compression methods methods=%s files=%d",
            method_summary,
            sum(methods.values()),
        )
        supported = {
            zipfile.ZIP_STORED,
            zipfile.ZIP_DEFLATED,
            zipfile.ZIP_BZIP2,
            zipfile.ZIP_LZMA,
        }
        if zstandard := getattr(zipfile, "ZIP_ZSTANDARD", None):
            supported.add(zstandard)
        unsupported = {
            method: count for method, count in methods.items() if method not in supported
        }
        if unsupported:
            unsupported_summary = _zip_compression_summary(unsupported)
            logger.warning("Remote ZIP compression unsupported methods=%s", unsupported_summary)
            raise NotImplementedError(
                f"Unsupported ZIP compression methods: {unsupported_summary}"
            )
        members = [
            ArchiveMember(
                name=info.filename,
                size=info.file_size,
                compressed_size=info.compress_size,
                is_directory=info.is_dir(),
            )
            for info in infos
        ]
        if any(_zip_is_link(info) for info in infos):
            raise ArchiveError("The archive contains a symbolic link.")
        total_size = sum(item.size for item in members if not item.is_directory)
        compressed_total = sum(
            item.compressed_size for item in members if not item.is_directory
        )
        file_count = sum(not item.is_directory for item in members)
        logger.info(
            "Remote ZIP metadata files=%d archive_size=%d compressed_members_size=%d "
            "extracted_total_size=%d",
            file_count,
            archive_size,
            compressed_total,
            total_size,
        )
        if validate_limits:
            self._validate_members(members, archive_size)
        return (
            ZipStreamMetadata(
                archive_size=archive_size,
                extracted_size=total_size,
                compressed_members_size=compressed_total,
                file_count=file_count,
            ),
            infos,
            members,
        )

    @staticmethod
    def _prepare_remote_resume_workspace(
        workspace: Path, infos: list[zipfile.ZipInfo]
    ) -> Path:
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / "extracted"
        marker = workspace / "archive.json"
        fingerprint = hashlib.sha256()
        for info in infos:
            fingerprint.update(info.filename.encode("utf-8", errors="surrogateescape"))
            fingerprint.update(
                f"\0{info.CRC}:{info.file_size}:{info.compress_size}:"
                f"{info.compress_type}:{info.header_offset}\n".encode()
            )
        metadata = {"version": 1, "fingerprint": fingerprint.hexdigest()}
        existing: object = None
        with suppress(FileNotFoundError, json.JSONDecodeError, OSError):
            existing = json.loads(marker.read_text(encoding="utf-8"))
        if existing not in (None, metadata):
            logger.warning("Remote ZIP changed; stale extraction checkpoint discarded")
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        temporary_marker = marker.with_suffix(".json.tmp")
        temporary_marker.write_text(json.dumps(metadata), encoding="utf-8")
        os.replace(temporary_marker, marker)
        return target

    def _validate_members(self, members: list[ArchiveMember], archive_size: int) -> None:
        files = [member for member in members if not member.is_directory]
        if len(files) > self.limits.max_files:
            raise ArchiveError("The archive contains too many files.")
        total = 0
        ratio_limit = self.limits.max_compression_ratio
        for member in members:
            _safe_member_path(member.name)
            total += member.size
            if ratio_limit is not None:
                if member.compressed_size == 0 and member.size > 0:
                    ratio = float("inf")
                elif member.compressed_size:
                    ratio = member.size / member.compressed_size
                else:
                    ratio = 1.0
                if ratio > ratio_limit:
                    raise ArchiveError("The archive has a suspicious compression ratio.")
        if total > self.limits.max_total_size:
            raise ArchiveError(
                "The archive exceeds the configured extracted-size limit: "
                f"{_format_size(total)} extracted, "
                f"{_format_size(self.limits.max_total_size)} allowed."
            )
        if ratio_limit is not None and archive_size and total / archive_size > ratio_limit:
            raise ArchiveError("The archive has a suspicious overall compression ratio.")

    @staticmethod
    def _extract_zip(
        archive: Path,
        target: Path,
        progress: ExtractionProgressCallback | None = None,
        total_size: int = 0,
    ) -> None:
        with zipfile.ZipFile(archive) as source:
            ArchiveExtractor._extract_zip_source(source, target, progress, total_size)

    @staticmethod
    def _extract_zip_source(
        source: zipfile.ZipFile,
        target: Path,
        progress: ExtractionProgressCallback | None = None,
        total_size: int = 0,
        *,
        resume: bool = False,
        resume_progress: ResumeProgressCallback | None = None,
    ) -> None:
        extracted = 0
        infos = source.infolist()
        completed_offsets: set[int] = set()
        if resume:
            ordered = sorted(infos, key=lambda item: item.header_offset)
            logical_offset = 0
            for index, info in enumerate(ordered):
                output = target / _safe_member_path(info.filename)
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    completed_offsets.add(info.header_offset)
                elif _completed_zip_output(output, info):
                    completed_offsets.add(info.header_offset)
                else:
                    break
                if index + 1 < len(ordered):
                    logical_offset = ordered[index + 1].header_offset
                else:
                    logical_offset = min(
                        source.start_dir,
                        info.header_offset + info.compress_size,
                    )
            if resume_progress:
                resume_progress(logical_offset, len(completed_offsets))
        for info in infos:
            output = target / _safe_member_path(info.filename)
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            if resume and info.header_offset in completed_offsets:
                extracted += info.file_size
                if progress:
                    progress(extracted, total_size)
                logger.info(
                    "Remote ZIP member restored from checkpoint member=%r size=%d",
                    info.filename,
                    info.file_size,
                )
                continue
            partial = output.with_name(output.name + ".part") if resume else output
            if resume:
                partial.unlink(missing_ok=True)
                output.unlink(missing_ok=True)
            with source.open(info) as input_file, partial.open("xb") as output_file:
                while chunk := input_file.read(1024 * 1024):
                    output_file.write(chunk)
                    extracted += len(chunk)
                    if progress:
                        progress(extracted, total_size)
            if resume:
                os.replace(partial, output)

    @staticmethod
    def _extract_tar(
        archive: Path,
        target: Path,
        progress: ExtractionProgressCallback | None = None,
        total_size: int = 0,
    ) -> None:
        extracted = 0
        with tarfile.open(archive, mode="r:*") as source:
            for info in source.getmembers():
                output = target / _safe_member_path(info.name)
                if info.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                input_file = source.extractfile(info)
                if input_file is None:
                    raise ArchiveError("A file in the archive could not be read.")
                output.parent.mkdir(parents=True, exist_ok=True)
                with input_file, output.open("xb") as output_file:
                    while chunk := input_file.read(1024 * 1024):
                        output_file.write(chunk)
                        extracted += len(chunk)
                        if progress:
                            progress(extracted, total_size)

    def _seven_zip(self) -> str:
        executable = self._find_seven_zip()
        if not executable:
            raise ArchiveError(
                "7-Zip/7zz is required for this archive's compression method. Install it from "
                "https://www.7-zip.org/ and try again."
            )
        return executable

    @staticmethod
    def _find_seven_zip() -> str | None:
        frozen_root = getattr(sys, "_MEIPASS", None)
        bundled_candidates = []
        if frozen_root:
            bundled_candidates.append(Path(frozen_root) / ".7zip" / "7z.exe")
        bundled_candidates.append(Path.cwd() / ".build-assets" / "7zip" / "7z.exe")
        executable = _first_existing(bundled_candidates)
        executable = executable or shutil.which("7zz") or shutil.which("7z")
        if not executable and os.name == "nt":
            executable = _first_existing(
                _windows_program_paths("7-Zip", "7z.exe")
            )
        return executable

    def _list_7zip(self, archive: Path) -> list[ArchiveMember]:
        executable = self._seven_zip()
        logger.info(
            "Running archive inspection tool=7-Zip executable=%s archive=%s",
            executable,
            archive,
        )
        process = subprocess.run(
            [executable, "l", "-slt", "--", str(archive)],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            creationflags=_subprocess_creation_flags(),
        )
        if process.returncode:
            detail = _process_detail(process)
            logger.error(
                "Archive inspection failed tool=7-Zip exit_code=%d detail=%r",
                process.returncode,
                detail,
            )
            raise ArchiveError(
                f"7-Zip could not inspect the archive (exit {process.returncode}): "
                f"{detail}"
            )
        members: list[ArchiveMember] = []
        current: dict[str, str] = {}
        for line in process.stdout.splitlines() + [""]:
            if " = " in line:
                key, value = line.split(" = ", 1)
                current[key] = value
                continue
            if not line and "Path" in current and "Size" in current:
                attributes = current.get("Attributes", "")
                members.append(
                    ArchiveMember(
                        name=current["Path"],
                        size=int(current["Size"]),
                        compressed_size=int(current.get("Packed Size", "0") or 0),
                        is_directory="D" in attributes,
                    )
                )
                current = {}
        if not members:
            raise ArchiveError("7-Zip returned no archive members.")
        return members

    def _extract_7zip(self, archive: Path, target: Path) -> None:
        executable = self._seven_zip()
        logger.info(
            "Running archive extraction tool=7-Zip executable=%s archive=%s target=%s",
            executable,
            archive,
            target,
        )
        process = subprocess.run(
            [executable, "x", "-y", f"-o{target}", "--", str(archive)],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=3600,
            creationflags=_subprocess_creation_flags(),
        )
        if process.returncode:
            detail = _process_detail(process)
            logger.error(
                "Archive extraction failed tool=7-Zip exit_code=%d detail=%r",
                process.returncode,
                detail,
            )
            raise ArchiveError(
                f"7-Zip could not extract the archive (exit {process.returncode}): "
                f"{detail}"
            )
        logger.info(
            "Archive extraction tool completed tool=7-Zip exit_code=0 "
            "archive_path_length=%d target_path_length=%d detail=%r",
            len(str(archive)),
            len(str(target)),
            _process_detail(process),
        )

    def _extract_rar(self, archive: Path, target: Path) -> None:
        unrar = shutil.which("unrar")
        if not unrar and os.name == "nt":
            unrar = _first_existing(
                _windows_program_paths("WinRAR", "UnRAR.exe")
            )
        unrar_failure: str | None = None
        if unrar:
            logger.info(
                "Running RAR extraction tool=unrar executable=%s archive=%s target=%s",
                unrar,
                archive,
                target,
            )
            process = subprocess.run(
                [
                    unrar,
                    "x",
                    "-y",
                    "-o-",
                    "-ol-",
                    "-c-",
                    "-idq",
                    str(archive),
                    f"{target}{os.sep}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=3600,
                creationflags=_subprocess_creation_flags(),
            )
            if process.returncode == 0:
                logger.info("Archive extraction tool completed tool=unrar exit_code=0")
                return
            detail = _process_detail(process)
            unrar_failure = f"unrar exit {process.returncode}: {detail}"
            logger.warning(
                "RAR extraction failed tool=unrar exit_code=%d detail=%r; "
                "trying 7-Zip fallback",
                process.returncode,
                detail,
            )
        else:
            logger.warning("RAR extraction tool unrar was not found; trying 7-Zip")

        try:
            self._extract_7zip(archive, target)
        except ArchiveError as exc:
            if unrar_failure is None:
                raise
            raise ArchiveError(
                f"RAR extraction failed. {unrar_failure}; 7-Zip fallback: {exc}"
            ) from exc

    def _validate_extracted_tree(self, target: Path) -> None:
        total = 0
        count = 0
        for path in target.rglob("*"):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ArchiveError("7-Zip extracted a symbolic link; output was removed.")
            if path.is_file():
                count += 1
                total += info.st_size
            if count > self.limits.max_files or total > self.limits.max_total_size:
                raise ArchiveError("Extracted output exceeded the configured safety limits.")


def _completed_zip_output(output: Path, info: zipfile.ZipInfo) -> bool:
    try:
        if not output.is_file() or output.stat().st_size != info.file_size:
            return False
        checksum = 0
        with output.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                checksum = zlib.crc32(chunk, checksum)
        return checksum & 0xFFFFFFFF == info.CRC
    except OSError:
        return False


def _storage_estimate(
    workspace: Path,
    destination_parent: Path,
    extracted_size: int,
    cache_budget: int,
) -> StorageEstimate:
    current_workspace_size = 0
    for path in workspace.rglob("*"):
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            current_workspace_size += info.st_size
    free_space = shutil.disk_usage(destination_parent).free
    safety_margin = max(
        256 * 1024**2,
        min(1024**3, extracted_size // 100),
    )
    peak_size = extracted_size + cache_budget
    additional_required = max(
        0,
        peak_size + safety_margin - current_workspace_size,
    )
    return StorageEstimate(
        extracted_size=extracted_size,
        cache_budget=cache_budget,
        peak_size=peak_size,
        safety_margin=safety_margin,
        current_workspace_size=current_workspace_size,
        free_space=free_space,
        additional_required=additional_required,
    )


def _format_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.2f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    raise AssertionError("unreachable")


def _safe_member_path(name: str) -> Path:
    if "\x00" in name or re.match(r"^[A-Za-z]:", name):
        raise ArchiveError("The archive contains an unsafe path.")
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise ArchiveError("The archive contains a path that leaves the destination.")
    parts = [part for part in pure.parts if part not in {"", "."}]
    if not parts:
        return Path(".")
    return Path(*parts)


def _zip_compression_summary(methods: dict[int, int] | Counter[int]) -> str:
    return ",".join(
        f"{method}:{_ZIP_COMPRESSION_NAMES.get(method, 'unknown')}={count}"
        for method, count in sorted(methods.items())
    )


def _zip_is_link(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _process_detail(process: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part for part in (process.stderr, process.stdout) if part)
    normalized = " ".join(output.split())
    if not normalized:
        return "no diagnostic output"
    return normalized[-1000:]


def _subprocess_creation_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _windows_program_paths(folder: str, executable: str) -> list[Path]:
    candidates = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / folder / executable)
    return candidates


def _first_existing(candidates: list[Path]) -> str | None:
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _archive_kind(path: Path) -> str:
    try:
        with path.open("rb") as source:
            header = source.read(8)
    except OSError as exc:
        raise ArchiveError("The archive could not be read.") from exc
    if zipfile.is_zipfile(path):
        return "zip"
    if tarfile.is_tarfile(path):
        return "tar"
    if header.startswith(b"7z\xbc\xaf'\x1c"):
        return "7z"
    if header.startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")):
        return "rar"
    raise ArchiveError("The file signature is not a supported ZIP, TAR, 7z, or RAR archive.")


def detect_archive_kind(path: Path) -> str:
    """Identify an archive from its contents instead of trusting its filename."""
    return _archive_kind(path)
