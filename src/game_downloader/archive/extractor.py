from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from game_downloader.models import ExtractionLimits, ExtractionResult

logger = logging.getLogger(__name__)


class ArchiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    size: int
    compressed_size: int
    is_directory: bool = False


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
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.extracting-", dir=destination.parent)
        )
        try:
            kind = _archive_kind(archive)
            if kind == "zip":
                self._extract_zip(archive, temporary)
            elif kind == "tar":
                self._extract_tar(archive, temporary)
            elif kind == "rar":
                self._extract_rar(archive, temporary)
                self._validate_extracted_tree(temporary)
            else:
                self._extract_7zip(archive, temporary)
                self._validate_extracted_tree(temporary)
            os.replace(temporary, destination)
        except Exception:
            logger.exception(
                "Archive extraction failed archive=%s destination=%s temporary=%s",
                archive,
                destination,
                temporary,
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
            total_size=sum(item.size for item in members if not item.is_directory),
        )

    def _validate_members(self, members: list[ArchiveMember], archive_size: int) -> None:
        files = [member for member in members if not member.is_directory]
        if len(files) > self.limits.max_files:
            raise ArchiveError("The archive contains too many files.")
        total = 0
        for member in members:
            _safe_member_path(member.name)
            total += member.size
            if member.compressed_size == 0 and member.size > 0:
                ratio = float("inf")
            elif member.compressed_size:
                ratio = member.size / member.compressed_size
            else:
                ratio = 1.0
            if ratio > self.limits.max_compression_ratio:
                raise ArchiveError("The archive has a suspicious compression ratio.")
        if total > self.limits.max_total_size:
            raise ArchiveError("The archive exceeds the configured extracted-size limit.")
        if archive_size and total / archive_size > self.limits.max_compression_ratio:
            raise ArchiveError("The archive has a suspicious overall compression ratio.")

    @staticmethod
    def _extract_zip(archive: Path, target: Path) -> None:
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                output = target / _safe_member_path(info.filename)
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                with source.open(info) as input_file, output.open("xb") as output_file:
                    shutil.copyfileobj(input_file, output_file, length=1024 * 1024)

    @staticmethod
    def _extract_tar(archive: Path, target: Path) -> None:
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
                    shutil.copyfileobj(input_file, output_file, length=1024 * 1024)

    def _seven_zip(self) -> str:
        executable = shutil.which("7zz") or shutil.which("7z")
        if not executable:
            raise ArchiveError(
                "7-Zip/7zz is required for RAR and 7z archives. Install it from "
                "https://www.7-zip.org/ and try again."
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
            check=False,
            timeout=3600,
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
        logger.info("Archive extraction tool completed tool=7-Zip exit_code=0")

    def _extract_rar(self, archive: Path, target: Path) -> None:
        unrar = shutil.which("unrar")
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


def _zip_is_link(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _process_detail(process: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part for part in (process.stderr, process.stdout) if part)
    normalized = " ".join(output.split())
    if not normalized:
        return "no diagnostic output"
    return normalized[-1000:]


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
