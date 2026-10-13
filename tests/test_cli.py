"""Tests for the pftree command line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pftree import __version__
from pftree.__main__ import main, parser_build


def tree_make(root: Path) -> Path:
    inputDir: Path = root / "in"
    (inputDir / "a").mkdir(parents=True)
    (inputDir / "a/x.dcm").write_bytes(b"x" * 10)
    (inputDir / "a/y.txt").write_bytes(b"y" * 20)
    return inputDir


def test_main_jsonPrintsTree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code: int = main(["--inputDir", str(tree_make(tmp_path)), "--relativeDir", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tree"] == {"a": ["x.dcm", "y.txt"]}


def test_main_fileFilterFromCli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--inputDir", str(tree_make(tmp_path)), "--relativeDir", "--fileFilter", "glob:*.dcm", "--json"])
    assert json.loads(capsys.readouterr().out)["tree"] == {"a": ["x.dcm"]}


def test_main_statsPrintsReport(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code: int = main(["--inputDir", str(tree_make(tmp_path)), "--relativeDir", "--stats", "--verbosity", "0"])
    out: str = capsys.readouterr().out
    assert code == 0
    assert "dir: a" in out
    assert "Total files:             2" in out


def test_main_dufPrintsLinesOnly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--inputDir", str(tree_make(tmp_path)), "--relativeDir", "--duf", "--verbosity", "0"])
    out: str = capsys.readouterr().out
    assert out.startswith("30.0B")
    assert "Total" not in out


def test_main_jsonStats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--inputDir", str(tree_make(tmp_path)), "--relativeDir", "--jsonStats"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["totalSize"] == 30


def test_main_testRunWritesOutput(tmp_path: Path) -> None:
    outputDir: Path = tmp_path / "out"
    code: int = main(
        [
            "--inputDir",
            str(tree_make(tmp_path)),
            "--outputDir",
            str(outputDir),
            "--outputLeafDir",
            "preview-%s",
            "--test",
            "0",
            "--verbosity",
            "0",
        ]
    )
    assert code == 0
    assert (outputDir / "preview-a/file-ls.txt").read_text().split() == ["x.dcm", "y.txt"]


def test_main_missingInputDirExitsOne(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code: int = main(["--inputDir", str(tmp_path / "nope"), "--verbosity", "0"])
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_main_badLeafTemplateIsUsageError(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--inputDir", str(tmp_path), "--outputLeafDir", "anon"])
    assert exc.value.code == 2


def test_main_versionExitsZero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_main_manAndSynopsisExitZero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--inputDir", ".", "--man"]) == 0
    assert "examples:" in capsys.readouterr().out
    assert main(["--inputDir", ".", "--synopsis"]) == 0
    assert capsys.readouterr().out.startswith("usage:")


def test_parser_build_dsOmitsIoArguments() -> None:
    standalone = parser_build(standalone=True)
    dsStyle = parser_build(standalone=False)
    assert standalone.parse_args(["--inputDir", "x"]).inputDir == "x"
    with pytest.raises(SystemExit):
        dsStyle.parse_args(["--inputDir", "x"])
    assert dsStyle.parse_args(["--threads", "2"]).threads == 2
