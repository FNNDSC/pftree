"""Tests for the Pftree engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from pftree.tree import DirNode, Pftree, StageResult, TreeOptions, size_format


def tree_make(root: Path) -> Path:
    """Build a small fixture tree and return its input root.

    Layout::

        in/
          top.txt
          sub-01/anat/a.nii.gz
          sub-01/anat/tmp.nii.gz
          sub-01/func/f.nii
          sub-02/anat/b.nii.gz
          empty/
    """
    inputDir: Path = root / "in"
    for rel in ("sub-01/anat", "sub-01/func", "sub-02/anat", "empty"):
        (inputDir / rel).mkdir(parents=True)
    (inputDir / "top.txt").write_text("top\n")
    (inputDir / "sub-01/anat/a.nii.gz").write_bytes(b"a" * 100)
    (inputDir / "sub-01/anat/tmp.nii.gz").write_bytes(b"t" * 50)
    (inputDir / "sub-01/func/f.nii").write_bytes(b"f" * 10)
    (inputDir / "sub-02/anat/b.nii.gz").write_bytes(b"b" * 200)
    return inputDir


def options_make(inputDir: Path, **extra: object) -> TreeOptions:
    """Options for the fixture tree, console silenced."""
    values: dict[str, object] = {"inputDir": inputDir, "outputDir": inputDir.parent / "out", "verbosity": 0}
    values.update(extra)
    return TreeOptions.fromDict(values)


def test_options_fromDict_coercesAndIgnoresUnknown(tmp_path: Path) -> None:
    options: TreeOptions = TreeOptions.fromDict(
        {"inputDir": str(tmp_path), "threads": "3", "relativeDir": True, "unknownKey": 1}
    )
    assert options.inputDir == tmp_path
    assert options.threads == 3
    assert options.relativeDir is True


def test_options_rejectBadLogic(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="AND or OR"):
        TreeOptions(inputDir=tmp_path, fileFilterLogic="NAND")


def test_options_rejectLeafTemplateWithoutPlaceholder(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="%s"):
        TreeOptions(inputDir=tmp_path, outputLeafDir="anon")


def test_probe_listsEveryDirWithFiles(tmp_path: Path) -> None:
    tree: Pftree = Pftree(options_make(tree_make(tmp_path)))
    result = tree.run()
    assert result.status
    keys: set[str] = {Path(key).relative_to(tree.options.inputDir).as_posix() for key in result.tree}
    assert keys == {".", "sub-01/anat", "sub-01/func", "sub-02/anat"}
    assert result.tree[str(tree.options.inputDir / "sub-01/anat")] == ["a.nii.gz", "tmp.nii.gz"]


def test_probe_relativeDirKeys(tmp_path: Path) -> None:
    result = Pftree(options_make(tree_make(tmp_path), relativeDir=True)).run()
    assert set(result.tree) == {".", "sub-01/anat", "sub-01/func", "sub-02/anat"}
    assert result.tree["."] == ["top.txt"]


def test_probe_maxDepthZeroIsRootOnly(tmp_path: Path) -> None:
    result = Pftree(options_make(tree_make(tmp_path), relativeDir=True, maxDepth=0)).run()
    assert set(result.tree) == {"."}


def test_probe_maxDepthOneStopsAtSubjects(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    (inputDir / "sub-01/notes.txt").write_text("n\n")
    result = Pftree(options_make(inputDir, relativeDir=True, maxDepth=1)).run()
    assert set(result.tree) == {".", "sub-01"}


def test_probe_inputFileNarrowsNames(tmp_path: Path) -> None:
    result = Pftree(options_make(tree_make(tmp_path), relativeDir=True, inputFile="tmp")).run()
    assert result.tree == {"sub-01/anat": ["tmp.nii.gz"]}


def test_filter_fileGlobDropsDirsLeftEmpty(tmp_path: Path) -> None:
    result = Pftree(options_make(tree_make(tmp_path), relativeDir=True, fileFilter="glob:*.nii.gz")).run()
    assert result.tree == {"sub-01/anat": ["a.nii.gz", "tmp.nii.gz"], "sub-02/anat": ["b.nii.gz"]}


def test_filter_fileAndWithNegation(tmp_path: Path) -> None:
    options: TreeOptions = options_make(
        tree_make(tmp_path), relativeDir=True, fileFilter="glob:*.nii.gz,!tmp", fileFilterLogic="and"
    )
    assert Pftree(options).run().tree == {"sub-01/anat": ["a.nii.gz"], "sub-02/anat": ["b.nii.gz"]}


def test_filter_dirRegexAndFileAreAnded(tmp_path: Path) -> None:
    options: TreeOptions = options_make(
        tree_make(tmp_path), relativeDir=True, dirFilter="re:/anat$", fileFilter="nii"
    )
    expected: dict[str, list[str]] = {"sub-01/anat": ["a.nii.gz", "tmp.nii.gz"], "sub-02/anat": ["b.nii.gz"]}
    assert Pftree(options).run().tree == expected


def test_filter_dirDoesNotMatchInputRootName(tmp_path: Path) -> None:
    result = Pftree(options_make(tree_make(tmp_path), relativeDir=True, dirFilter="in")).run()
    assert result.tree == {}


def test_outputPath_mirrorsAndRenamesLeaf(tmp_path: Path) -> None:
    tree: Pftree = Pftree(options_make(tree_make(tmp_path), relativeDir=True, outputLeafDir="preview-%s"))
    tree.tree_construct(tree.tree_probe())
    node: DirNode = tree.nodes["sub-01/anat"]
    assert node.outputPath == tree.options.outputDir / "sub-01/preview-anat"
    assert tree.nodes["."].outputPath == tree.options.outputDir


def test_testRun_writesListingPerDir(tmp_path: Path) -> None:
    tree: Pftree = Pftree(options_make(tree_make(tmp_path), relativeDir=True, test="0"))
    result = tree.run()
    assert result.status
    assert result.process is not None
    assert result.process.filesRead == 5
    assert result.process.filesAnalyzed == 5
    assert result.process.filesSaved == 4
    listing: Path = tree.options.outputDir / "sub-01/anat/file-ls.txt"
    assert listing.read_text().split() == ["a.nii.gz", "tmp.nii.gz"]


def test_testRun_typeOneWritesCounts(tmp_path: Path) -> None:
    tree: Pftree = Pftree(options_make(tree_make(tmp_path), relativeDir=True, test="0:1"))
    tree.run()
    assert (tree.options.outputDir / "sub-02/anat/file-count.txt").read_text() == "1\n"


def test_testRun_threadedMatchesSequential(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    sequential = Pftree(options_make(inputDir, relativeDir=True, test="0")).run()
    threaded = Pftree(options_make(inputDir, relativeDir=True, test="0", threads=3)).run()
    assert threaded.process is not None and sequential.process is not None
    assert threaded.process.threaded and not sequential.process.threaded
    assert threaded.tree == sequential.tree
    assert threaded.process.filesAnalyzed == sequential.process.filesAnalyzed


class FailingAnat(Pftree):
    """Fails the analyze stage for any directory ending in ``anat``."""

    def inputAnalyze_callback(self, node: DirNode, read: StageResult) -> StageResult:
        if node.relPath.endswith("anat"):
            raise RuntimeError("anat is broken")
        return super().inputAnalyze_callback(node, read)


def test_process_exceptionDropsDirAndRunContinues(tmp_path: Path) -> None:
    tree: FailingAnat = FailingAnat(options_make(tree_make(tmp_path), relativeDir=True, test="0"))
    result = tree.run()
    assert result.status
    assert set(result.tree) == {".", "sub-01/func"}
    assert result.process is not None
    assert result.process.analyze["sub-01/anat"].status is False
    assert set(result.process.write) == {".", "sub-01/func"}


def test_process_allFailuresMeanFailedStatus(tmp_path: Path) -> None:
    options: TreeOptions = options_make(tree_make(tmp_path), relativeDir=True, test="0", dirFilter="anat")
    result = FailingAnat(options).run()
    assert not result.status
    assert result.tree == {}


def test_stats_totalsAndOrder(tmp_path: Path) -> None:
    result = Pftree(options_make(tree_make(tmp_path), relativeDir=True, stats=True)).run()
    assert result.stats is not None
    assert result.stats.dirs == 4
    assert result.stats.files == 5
    assert result.stats.totalSize == 100 + 50 + 10 + 200 + 4
    assert [stat.path for stat in result.stats.perDir][-1] == "sub-02/anat"
    reverse = Pftree(options_make(tree_make(tmp_path / "r"), relativeDir=True, statsReverse=True)).run()
    assert reverse.stats is not None
    assert reverse.stats.perDir[0].path == "sub-02/anat"


def test_stats_skipsSymlinks(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    (inputDir / "sub-02/anat/link.nii.gz").symlink_to(inputDir / "sub-01/anat/a.nii.gz")
    result = Pftree(options_make(inputDir, relativeDir=True, stats=True)).run()
    assert result.stats is not None
    anat = next(stat for stat in result.stats.perDir if stat.path == "sub-02/anat")
    assert anat.files == 2
    assert anat.sizeBytes == 200


def test_followLinks_descendsLinkedDir(tmp_path: Path) -> None:
    inputDir: Path = tree_make(tmp_path)
    elsewhere: Path = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "x.txt").write_text("x\n")
    (inputDir / "linked").symlink_to(elsewhere, target_is_directory=True)
    without = Pftree(options_make(inputDir, relativeDir=True)).run()
    with_links = Pftree(options_make(inputDir, relativeDir=True, followLinks=True)).run()
    assert "linked" not in without.tree
    assert with_links.tree["linked"] == ["x.txt"]


def test_run_missingInputDirFails(tmp_path: Path) -> None:
    result = Pftree(options_make(tmp_path / "nope")).run()
    assert not result.status
    assert "not found" in result.error


def test_run_toDictIsJsonReady(tmp_path: Path) -> None:
    result = Pftree(options_make(tree_make(tmp_path), relativeDir=True, stats=True)).run()
    view: dict[str, object] = result.toDict()
    assert view["status"] is True
    assert isinstance(view["stats"], dict)


def test_size_format() -> None:
    assert size_format(512) == "512.0B"
    assert size_format(1536) == "1.5kB"
    assert size_format(3 * 1024**3) == "3.0GB"
