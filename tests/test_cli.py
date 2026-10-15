"""Tests for the pftree command line."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from pftree import __version__
from pftree.__main__ import main, manual_build, parser_build
from pftree.manual import (
    BG_PAGE,
    FG_BANNER,
    FG_BODY,
    FG_COMMAND,
    FG_CONTINUATION,
    FG_FLAG,
    FG_HEADING,
    FG_PLACEHOLDER,
    FG_STRING,
    FG_VERSION,
    NO_COLOUR,
    ManualAction,
    banner_render,
    cliLine_highlight,
    inlineCode_render,
    synopsis_colourize,
)


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


def test_main_duPrintsDuLines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inputDir: Path = tree_make(tmp_path)
    main(["--inputDir", str(inputDir), "--du", "--verbosity", "0"])
    lines: list[str] = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].endswith(f"\t{inputDir / 'a'}")
    assert lines[1].endswith(f"\t{inputDir}")
    assert all(line.split("\t")[0].isdigit() for line in lines)
    assert "Total" not in "\n".join(lines)


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


def test_main_inputDirRequiredUnlessManual() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--stats"])
    assert exc.value.code == 2


def manual_run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    return int(exc.value.code or 0)


def test_main_manPrintsFullManual(capsys: pytest.CaptureFixture[str]) -> None:
    assert manual_run(["--man"]) == 0
    out: str = capsys.readouterr().out
    assert "Path-File tree structure" in out
    assert __version__ in out
    assert "DESCRIPTION" in out
    assert "[--fileFilter <token1,token2,...>]" in out
    assert "glob:*.nii.gz" in out
    assert "EXAMPLES" in out
    assert "\033[" not in out


def test_cliLine_highlight_coloursEachTokenKind() -> None:
    line: str = "        pftree --inputDir <inputDir> --fileFilter 'glob:*.dcm' \\"
    coloured: str = cliLine_highlight(line, manual_build().cliToken_pattern())
    assert FG_COMMAND + "pftree" in coloured
    assert FG_FLAG + "--inputDir" in coloured
    assert FG_PLACEHOLDER + "<inputDir>" in coloured
    assert FG_STRING + "'glob:*.dcm'" in coloured
    assert coloured.endswith(FG_CONTINUATION + "\\" + FG_BODY)


def test_banner_render_coloursArtTitleProseAndVersion() -> None:
    page: list[str] = banner_render(manual_build(), colour=True, width=80).splitlines()
    assert page[2].startswith(BG_PAGE + FG_BANNER)
    title: str = next(line for line in page if "Path-File tree structure" in line)
    assert title.startswith(BG_PAGE + FG_HEADING)
    prose: str = next(line for line in page if "Recursively walk" in line)
    assert prose.startswith(BG_PAGE + FG_BODY)
    version: str = next(line for line in page if __version__ in line)
    assert FG_VERSION + __version__ + FG_BODY in version
    bullet: str = next(line for line in page if "read input files" in line)
    assert FG_FLAG + "*" + FG_BODY in bullet
    assert banner_render(manual_build(), colour=False).count("\033") == 0


def test_inlineCode_render_dropsBackticksAndColours() -> None:
    line: str = "        ``pftree`` in and of itself does not really do any work."
    assert (
        inlineCode_render(line, colour=False)
        == "        pftree in and of itself does not really do any work."
    )
    assert FG_FLAG + "pftree" + FG_BODY in inlineCode_render(line, colour=True)


def test_main_manHasNoBackticksWhenPiped(capsys: pytest.CaptureFixture[str]) -> None:
    manual_run(["--man"])
    assert "``" not in capsys.readouterr().out


def test_manual_colourize_padsEveryLineToWidth() -> None:
    page: str = synopsis_colourize(manual_build(), "    NAME\n\n        pftree - x\n", width=40)
    for line in page.splitlines():
        assert line.startswith(BG_PAGE)
        assert line.endswith(NO_COLOUR)
        visible: str = re.sub(r"\033\[[0-9;]*m", "", line)
        assert len(visible) == 40
    assert page.splitlines()[0].startswith(BG_PAGE + FG_HEADING)


def test_main_synopsisPrintsBriefManual(capsys: pytest.CaptureFixture[str]) -> None:
    assert manual_run(["--synopsis"]) == 0
    out: str = capsys.readouterr().out
    assert "SYNOPSIS" in out
    assert "BRIEF EXAMPLE" in out
    assert "DESCRIPTION" not in out


def test_parser_dsStyleManNeedsNoDirectories(capsys: pytest.CaptureFixture[str]) -> None:
    parser = parser_build(standalone=False)
    parser.add_argument("inputdir")
    parser.add_argument("outputdir")
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--man"])
    assert exc.value.code == 0
    assert "DESCRIPTION" in capsys.readouterr().out


def test_manualAction_storesTrueWithoutManual() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--man", dest="man", action=ManualAction)
    assert parser.parse_args(["--man"]).man is True
    assert parser.parse_args([]).man is False


def test_manualAction_isStoreTrueForChrisPlugin() -> None:
    assert isinstance(ManualAction(["--man"], "man"), argparse._StoreTrueAction)


def test_main_helpPointsAtMan(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "pftree --man" in capsys.readouterr().out


def test_parser_build_dsOmitsIoArguments() -> None:
    standalone = parser_build(standalone=True)
    dsStyle = parser_build(standalone=False)
    assert standalone.parse_args(["--inputDir", "x"]).inputDir == "x"
    with pytest.raises(SystemExit):
        dsStyle.parse_args(["--inputDir", "x"])
    assert dsStyle.parse_args(["--threads", "2"]).threads == 2
