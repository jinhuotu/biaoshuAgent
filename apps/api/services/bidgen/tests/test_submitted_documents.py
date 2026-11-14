import shutil
from pathlib import Path

import pytest

from api.services.bidgen.service import (
    SUBMITTED_ARCHIVE_NAME,
    SUBMITTED_DOCUMENT_NAMES,
    SUBMITTED_GENERATION_DELAY_ENV,
    SUBMITTED_GENERATION_DELAY_SECONDS,
    _submitted_document_paths,
    _submitted_generation_delay,
)


@pytest.fixture
def submitted_dir(monkeypatch):
    folder = Path("storage") / "bidgen-special-test"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    monkeypatch.setenv("BIDGEN_USE_SUBMITTED_DOCUMENTS", "true")
    monkeypatch.setenv("BIDGEN_SUBMITTED_DOCS_DIR", str(folder))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _write_submitted_documents(folder):
    contents = {}
    for key, name in SUBMITTED_DOCUMENT_NAMES.items():
        contents[key] = f"submitted-{key}".encode()
        (folder / name).write_bytes(contents[key])
    return contents


def test_submitted_documents_match_only_the_configured_archive(submitted_dir):
    expected = _write_submitted_documents(submitted_dir)

    paths = _submitted_document_paths(SUBMITTED_ARCHIVE_NAME)

    assert {key: path.read_bytes() for key, path in paths.items()} == expected
    assert _submitted_document_paths("other-tender.zip") is None


def test_submitted_document_shortcut_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BIDGEN_USE_SUBMITTED_DOCUMENTS", raising=False)

    assert _submitted_document_paths(SUBMITTED_ARCHIVE_NAME) is None
    assert _submitted_generation_delay(SUBMITTED_ARCHIVE_NAME) == 0.0


def test_submitted_documents_fail_before_generation_when_a_file_is_missing(submitted_dir):
    _write_submitted_documents(submitted_dir)
    missing = submitted_dir / SUBMITTED_DOCUMENT_NAMES["technical"]
    missing.unlink()

    with pytest.raises(ValueError, match="缺少文件"):
        _submitted_document_paths(SUBMITTED_ARCHIVE_NAME)


def test_only_special_archive_uses_the_configured_generation_delay(monkeypatch):
    monkeypatch.setenv("BIDGEN_USE_SUBMITTED_DOCUMENTS", "true")
    assert _submitted_generation_delay(SUBMITTED_ARCHIVE_NAME) == SUBMITTED_GENERATION_DELAY_SECONDS
    assert _submitted_generation_delay("other-tender.zip") == 0.0

    monkeypatch.setenv(SUBMITTED_GENERATION_DELAY_ENV, "0")
    assert _submitted_generation_delay(SUBMITTED_ARCHIVE_NAME) == 0.0
