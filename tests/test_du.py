"""Tests for the ``du`` report: it must print what GNU du prints."""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pftree.__main__ import main
from pftree.models import TreeOptions
from pftree.tree import Pftree, TreeScanner, du_humanize, gil_isEnabled, kilobytes_roundUp

DU: str | None = shutil.which("du")


def tree_make(root: Path) -> Path:
    """A tree with nesting, an empty directory, a hard link and a symlink."""
    inputDir: Path = root / "in"
    for rel in ("a/deep/deeper", "b", "empty"):
        (inputDir / rel).mkdir(parents=True)
    (inputDir / "top.bin").write_bytes(os.urandom(5000))
    (inputDir / "a/one.bin").write_bytes(os.urandom(70000))
    (inputDir / "a/deep/two.bin").write_bytes(os.urandom(1234))
    (inputDir / "a/deep/deeper/three.bin").write_bytes(os.urandom(300000))
    (inputDir / "b/four.bin").write_bytes(os.urandom(9000))
    os.link(inputDir / "a/one.bin", inputDir / "b/one-again.bin")
    (inputDir / "b/link").symlink_to(inputDir / "a/one.bin")
    return inputDir


def du_lines(inputDir: Path, *flags: str) -> list[str]:
    assert DU is not None
    completed = subprocess.run([DU, *flags, str(inputDir)], capture_output=True, text=True, check=True)
    return completed.stdout.splitlines()


def pftree_lines(inputDir: Path, *flags: str, capsys: pytest.CaptureFixture[str]) -> list[str]:
    assert main(["--inputDir", str(inputDir), "--verbosity", "0", *flags]) == 0
    return capsys.readouterr().out.splitlines()


def lines_assertSame(actual: list[str], expected: list[str]) -> None:
    """Compare as sets of lines, showing the full diff and the du version on failure."""
    version: str = subprocess.run([str(DU), "--version"], capture_output=True, text=True).stdout.splitlines()[
        0
    ]
    diff: str = "\n".join(difflib.unified_diff(sorted(expected), sorted(actual), "du", "pftree", lineterm=""))
    assert sorted(actual) == sorted(expected), f"{version}\n{diff}"


@pytest.mark.skipif(DU is None, reason="GNU du not installed")
def test_du_matchesDuK(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inputDir: Path = tree_make(tmp_path)
    expected: list[str] = du_lines(inputDir, "-k")
    actual: list[str] = pftree_lines(inputDir, "--du", capsys=capsys)
    lines_assertSame(actual, expected)
    assert actual[-1] == expected[-1]


@pytest.mark.skipif(DU is None, reason="GNU du not installed")
def test_du_apparentSizeMatchesDu(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inputDir: Path = tree_make(tmp_path)
    expected: list[str] = du_lines(inputDir, "-k", "--apparent-size")
    actual: list[str] = pftree_lines(inputDir, "--du", "--apparentSize", capsys=capsys)
    lines_assertSame(actual, expected)


@pytest.mark.skipif(DU is None, reason="GNU du not installed")
def test_duf_matchesDuH(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inputDir: Path = tree_make(tmp_path)
    expected: list[str] = du_lines(inputDir, "-h")
    actual: list[str] = pftree_lines(inputDir, "--duf", capsys=capsys)
    lines_assertSame(actual, expected)


@pytest.mark.skipif(DU is None, reason="GNU du not installed")
def test_duf_apparentMatchesDuH(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inputDir: Path = tree_make(tmp_path)
    expected: list[str] = du_lines(inputDir, "-h", "--apparent-size")
    actual: list[str] = pftree_lines(inputDir, "--duf", "--apparentSize", capsys=capsys)
    lines_assertSame(actual, expected)


@pytest.mark.skipif(DU is None, reason="GNU du not installed")
def test_du_followLinksMatchesDuL(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inputDir: Path = tree_make(tmp_path)
    elsewhere: Path = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "x.bin").write_bytes(os.urandom(20000))
    (inputDir / "linked").symlink_to(elsewhere, target_is_directory=True)
    expected: list[str] = du_lines(inputDir, "-k", "-L")
    actual: list[str] = pftree_lines(inputDir, "--du", "--followLinks", capsys=capsys)
    lines_assertSame(actual, expected)


def test_scan_postOrderRootLastAndEveryDir(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    scan = TreeScanner(inputDir, maxDepth=-1, followLinks=False).scan()
    paths: list[str] = [str(usage.path.relative_to(inputDir)) for usage in scan.usage]
    assert paths[-1] == "."
    assert "empty" in paths
    assert paths.index("a/deep/deeper") < paths.index("a/deep") < paths.index("a")
    assert len(scan.entries) == len(scan.usage) == 6


def test_scan_hardLinkCountedOnce(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    scan = TreeScanner(inputDir, maxDepth=-1, followLinks=False).scan()
    root = scan.usage[-1]
    fileBytes: int = sum(
        size.apparentBytes for entry in scan.entries for size in entry.fileSizes.values() if not size.isLink
    )
    assert root.apparentBytes < fileBytes


def test_scan_maxDepthExcludesDeeperLevels(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    full = TreeScanner(inputDir, maxDepth=-1, followLinks=False).scan().usage[-1]
    shallow = TreeScanner(inputDir, maxDepth=1, followLinks=False).scan().usage[-1]
    assert shallow.apparentBytes < full.apparentBytes


def test_stats_usesScanSizes(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    result = Pftree(TreeOptions(inputDir=inputDir, relativeDir=True, stats=True, verbosity=0)).run()
    assert result.stats is not None
    bStat = next(stat for stat in result.stats.perDir if stat.path == "b")
    assert bStat.files == 3
    assert bStat.sizeBytes == 9000 + 70000


def test_kilobytes_roundUp() -> None:
    assert kilobytes_roundUp(0) == 0
    assert kilobytes_roundUp(1) == 1
    assert kilobytes_roundUp(1024) == 1
    assert kilobytes_roundUp(1025) == 2


def test_du_humanize_matchesDuStyle() -> None:
    assert du_humanize(0) == "0"
    assert du_humanize(512) == "512"
    assert du_humanize(4096) == "4.0K"
    assert du_humanize(12 * 1024) == "12K"
    assert du_humanize(1536 * 1024) == "1.5M"
    assert du_humanize(1500 * 1024) == "1.5M"
    assert du_humanize(3 * 1024**3) == "3.0G"
    assert du_humanize(10 * 1024 - 1) == "10K"


def test_scan_threadedMatchesSequential(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    (inputDir / "b/one-again.bin").unlink()  # a hard link shared across subtrees is charged by timing
    sequential = TreeScanner(inputDir, maxDepth=-1, followLinks=False).scan()
    threaded = TreeScanner(inputDir, maxDepth=-1, followLinks=False, threads=4).scan()
    assert [entry.path for entry in threaded.entries] == [entry.path for entry in sequential.entries]
    assert [usage.path for usage in threaded.usage] == [usage.path for usage in sequential.usage]
    assert threaded.usage[-1] == sequential.usage[-1]
    assert sum(usage.diskBytes for usage in threaded.usage) == sum(
        usage.diskBytes for usage in sequential.usage
    )


@pytest.mark.skipif(DU is None, reason="GNU du not installed")
def test_du_threadedMatchesDuTotals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inputDir: Path = tree_make(tmp_path)
    expected: list[str] = du_lines(inputDir, "-k")
    threaded = TreeScanner(inputDir, maxDepth=-1, followLinks=False, threads=3).scan()
    actualRoot: int = kilobytes_roundUp(threaded.usage[-1].diskBytes)
    assert f"{actualRoot}\t{inputDir}" == expected[-1]


def test_scanThreads_gatedOnGil(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    inputDir: Path = tree_make(tmp_path)
    tree: Pftree = Pftree(TreeOptions(inputDir=inputDir, threads=4, verbosity=0))
    with caplog.at_level("WARNING", logger="pftree"):
        scanThreads: int = tree.scanThreads_get()
    if gil_isEnabled():
        assert scanThreads == 1
        assert "free-threaded" in caplog.text
    else:
        assert scanThreads == 4
    assert tree.run().status


@pytest.mark.parametrize("splitDepth", [1, 2, 3, 9])
def test_scan_splitLevelsMatchSequential(tmp_path: Path, splitDepth: int) -> None:
    inputDir: Path = tree_make(tmp_path)
    (inputDir / "b/one-again.bin").unlink()  # a hard link shared across subtrees is charged by timing
    sequential = TreeScanner(inputDir, maxDepth=-1, followLinks=False).scan()
    threaded = TreeScanner(inputDir, maxDepth=-1, followLinks=False, threads=4, splitDepth=splitDepth).scan()
    assert [entry.path for entry in threaded.entries] == [entry.path for entry in sequential.entries]
    assert threaded.usage == sequential.usage


def test_scan_splitClampsToMaxDepth(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    sequential = TreeScanner(inputDir, maxDepth=1, followLinks=False).scan()
    scanner = TreeScanner(inputDir, maxDepth=1, followLinks=False, threads=4, splitDepth=3)
    assert scanner.splitDepth == 1
    assert scanner.scan().usage == sequential.usage
    rootOnly = TreeScanner(inputDir, maxDepth=0, followLinks=False, threads=4, splitDepth=2).scan()
    assert len(rootOnly.usage) == 1
