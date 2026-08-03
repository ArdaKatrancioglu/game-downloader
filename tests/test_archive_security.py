import io
import stat
import subprocess
import tarfile
import zipfile

import pytest

from game_downloader.archive.extractor import (
    ArchiveError,
    ArchiveExtractor,
    detect_archive_kind,
)
from game_downloader.models import ExtractionLimits
from game_downloader.ui.main_window import _available_extraction_destination


def write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)


def test_extracts_safe_zip_via_temporary_directory(tmp_path):
    archive = tmp_path / "safe.zip"
    write_zip(archive, [("folder/readme.txt", b"authorized demo")])
    destination = tmp_path / "output"
    result = ArchiveExtractor().extract(archive, destination)
    assert (destination / "folder/readme.txt").read_bytes() == b"authorized demo"
    assert result.file_count == 1


def test_zip_extraction_reports_byte_progress(tmp_path):
    archive = tmp_path / "progress.zip"
    payload = b"authorized demo" * 100
    write_zip(archive, [("game/data.bin", payload)])
    reported = []

    ArchiveExtractor().extract(
        archive,
        tmp_path / "output",
        progress=lambda extracted, total: reported.append((extracted, total)),
    )

    assert reported[0] == (0, len(payload))
    assert reported[-1] == (len(payload), len(payload))


def test_unsupported_zip_method_falls_back_to_7zip(monkeypatch, tmp_path):
    archive = tmp_path / "unsupported-method.zip"
    write_zip(archive, [("game/data.bin", b"payload")])
    destination = tmp_path / "output"
    calls = []

    def unsupported(*args, **kwargs):
        raise NotImplementedError("That compression method is not supported")

    def extract_with_7zip(_archive, target):
        calls.append((_archive, target))
        (target / "game").mkdir()
        (target / "game" / "data.bin").write_bytes(b"payload")

    extractor = ArchiveExtractor()
    monkeypatch.setattr(extractor, "_find_seven_zip", lambda: None)
    monkeypatch.setattr(extractor, "_extract_zip", unsupported)
    monkeypatch.setattr(extractor, "_extract_7zip", extract_with_7zip)

    result = extractor.extract(archive, destination)

    assert calls
    assert (destination / "game" / "data.bin").read_bytes() == b"payload"
    assert result.total_size == len(b"payload")


def test_zip_prefers_7zip_when_available(monkeypatch, tmp_path):
    archive = tmp_path / "long-path.zip"
    write_zip(archive, [("game/data.bin", b"payload")])
    destination = tmp_path / "output"
    calls = []

    def extract_with_7zip(_archive, target):
        calls.append("7zip")
        (target / "game").mkdir()
        (target / "game" / "data.bin").write_bytes(b"payload")

    extractor = ArchiveExtractor()
    monkeypatch.setattr(extractor, "_find_seven_zip", lambda: "/tools/7z")
    monkeypatch.setattr(extractor, "_extract_7zip", extract_with_7zip)
    monkeypatch.setattr(
        extractor,
        "_extract_zip",
        lambda *args, **kwargs: pytest.fail("Python ZIP extractor must not run"),
    )

    extractor.extract(archive, destination)

    assert calls == ["7zip"]


def test_bundled_7zip_is_preferred_in_frozen_app(monkeypatch, tmp_path):
    executable = tmp_path / ".7zip" / "7z.exe"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setattr(
        "game_downloader.archive.extractor.sys._MEIPASS",
        str(tmp_path),
        raising=False,
    )
    monkeypatch.setattr("game_downloader.archive.extractor.shutil.which", lambda _name: None)

    assert ArchiveExtractor()._seven_zip() == str(executable)


def test_detects_imported_browser_download_by_signature(tmp_path):
    archive = tmp_path / "browser-download-without-extension"
    write_zip(archive, [("readme.txt", b"authorized")])
    assert detect_archive_kind(archive) == "zip"


def test_extraction_destination_preserves_existing_output(tmp_path):
    archive = tmp_path / "owned.release.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00")
    (tmp_path / "owned.release-extracted").mkdir()

    assert _available_extraction_destination(archive) == (
        tmp_path / "owned.release-extracted (2)"
    )


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "C:\\escape.txt"])
def test_rejects_zip_slip_and_absolute_paths(tmp_path, name):
    archive = tmp_path / "attack.zip"
    write_zip(archive, [(name, b"bad")])
    with pytest.raises(ArchiveError, match="path"):
        ArchiveExtractor().extract(archive, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_rejects_zip_symlink(tmp_path):
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as output:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(info, "../outside")
    with pytest.raises(ArchiveError, match="symbolic link"):
        ArchiveExtractor().list_contents(archive)


def test_rejects_tar_symlink(tmp_path):
    archive = tmp_path / "link.tar"
    with tarfile.open(archive, "w") as output:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../outside"
        output.addfile(info)
    with pytest.raises(ArchiveError, match="link"):
        ArchiveExtractor().list_contents(archive)


def test_rejects_excessive_file_count(tmp_path):
    archive = tmp_path / "many.zip"
    write_zip(archive, [(f"{index}.txt", b"x") for index in range(3)])
    extractor = ArchiveExtractor(ExtractionLimits(max_files=2))
    with pytest.raises(ArchiveError, match="too many"):
        extractor.list_contents(archive)


def test_rejects_archive_bomb_ratio(tmp_path):
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("zeros.bin", b"\0" * 100_000)
    extractor = ArchiveExtractor(ExtractionLimits(max_compression_ratio=5))
    with pytest.raises(ArchiveError, match="compression ratio"):
        extractor.list_contents(archive)


def test_default_limits_allow_high_compression_ratio(tmp_path):
    archive = tmp_path / "high-ratio.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("zeros.bin", b"\0" * 100_000)

    assert ArchiveExtractor().list_contents(archive)[0].name == "zeros.bin"


def test_rejects_existing_nonempty_destination_even_with_overwrite(tmp_path):
    archive = tmp_path / "safe.zip"
    write_zip(archive, [("file.txt", b"new")])
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / "owned.txt").write_text("keep")
    with pytest.raises(ArchiveError, match="choose a new or empty"):
        ArchiveExtractor().extract(archive, destination, overwrite=True)
    assert (destination / "owned.txt").read_text() == "keep"


def test_rejects_tar_size_limit_before_extracting(tmp_path):
    archive = tmp_path / "large.tar"
    with tarfile.open(archive, "w") as output:
        info = tarfile.TarInfo("large.bin")
        payload = b"a" * 20
        info.size = len(payload)
        output.addfile(info, io.BytesIO(payload))
    extractor = ArchiveExtractor(ExtractionLimits(max_total_size=10))
    with pytest.raises(ArchiveError, match="size limit"):
        extractor.extract(archive, tmp_path / "output")


def test_rar_extraction_prefers_unrar(monkeypatch, tmp_path):
    archive = tmp_path / "owned.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00")
    target = tmp_path / "output"
    target.mkdir()
    commands = []

    monkeypatch.setattr(
        "game_downloader.archive.extractor.shutil.which",
        lambda name: f"/tools/{name}",
    )

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("game_downloader.archive.extractor.subprocess.run", run)
    ArchiveExtractor()._extract_rar(archive, target)

    assert commands[0][0][0] == "/tools/unrar"
    assert commands[0][0][1:6] == ["x", "-y", "-o-", "-ol-", "-c-"]
    assert len(commands) == 1


def test_rar_extraction_falls_back_to_7zip_and_reports_both_failures(
    monkeypatch,
    tmp_path,
):
    archive = tmp_path / "owned.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00")
    target = tmp_path / "output"
    target.mkdir()

    monkeypatch.setattr(
        "game_downloader.archive.extractor.shutil.which",
        lambda name: f"/tools/{name}",
    )

    def run(command, **kwargs):
        if command[0].endswith("unrar"):
            return subprocess.CompletedProcess(command, 3, "", "CRC failed")
        return subprocess.CompletedProcess(command, 2, "", "Data error")

    monkeypatch.setattr("game_downloader.archive.extractor.subprocess.run", run)

    with pytest.raises(ArchiveError) as error:
        ArchiveExtractor()._extract_rar(archive, target)

    message = str(error.value)
    assert "unrar exit 3: CRC failed" in message
    assert "7-Zip fallback" in message
    assert "Data error" in message
