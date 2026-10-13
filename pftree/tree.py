"""The pftree engine: probe a directory tree, filter it, process it, report on it.

The engine turns an input directory into ``tree``, a mapping from each directory that
holds files to the names of those files, then runs up to three callbacks over every
directory: read, analyze, write. Reads and writes run sequentially; the analyze stage
can run in a thread pool. Output for a directory goes to the same relative location
under the output directory, optionally with a renamed leaf.

Key components:
    - Pftree: the engine; subclass it and override the three ``*_callback`` methods
    - tree_walk: a depth-bounded ``os.walk`` yielding WalkEntry models
    - size_format: bytes to a human-readable size
    - stage_succeeded: the per-stage status rule

Typical usage:
    class Mine(Pftree):
        def inputAnalyze_callback(self, node: DirNode, read: StageResult) -> StageResult:
            ...
    Mine(TreeOptions(inputDir=Path("/in"), outputDir=Path("/out"))).run()

Dependencies:
    - tqdm, for progress bars on the console
    - pftree.filters, for the file and directory filters
    - pftree.models, for every option and result type
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path, PurePosixPath

from tqdm import tqdm

from pftree.filters import (
    FilterLogic,
    FilterToken,
    dirPath_matches,
    fileNames_filter,
    tokens_parse,
)
from pftree.models import (
    WALK_ALL_DEPTHS,
    DirNode,
    DirStat,
    KeyedResult,
    ProcessResult,
    RunResult,
    StageResult,
    StatsResult,
    TreeOptions,
    WalkEntry,
)

logger: logging.Logger = logging.getLogger("pftree")

TEST_FILELIST_NAME: str = "file-ls.txt"
TEST_FILECOUNT_NAME: str = "file-count.txt"
SIZE_UNITS: tuple[str, ...] = ("", "k", "M", "G", "T", "P", "E", "Z")
SIZE_STEP: float = 1024.0

ReadCallback = Callable[[DirNode], StageResult]
AnalyzeCallback = Callable[[DirNode, StageResult], StageResult]
WriteCallback = Callable[[DirNode, StageResult], StageResult]


def size_format(byteCount: float) -> str:
    """Render a byte count with a binary unit prefix.

    Args:
        byteCount: the size in bytes.

    Returns:
        ``"512.0B"``, ``"1.5kB"``, ``"3.2GB"`` and so on.
    """
    size: float = float(byteCount)
    for unit in SIZE_UNITS:
        if abs(size) < SIZE_STEP:
            return f"{size:.1f}{unit}B"
        size /= SIZE_STEP
    return f"{size:.1f}YiB"


def tree_walk(root: Path, maxDepth: int, followLinks: bool) -> Iterator[WalkEntry]:
    """Walk a directory to a bounded depth.

    Args:
        root: where to start.
        maxDepth: levels below ``root`` to descend; ``-1`` for all, ``0`` for root only.
        followLinks: follow symbolic links to directories.

    Yields:
        Each directory and its file names, root first, as ``os.walk`` orders them.
    """
    for current, dirNames, fileNames in os.walk(root, followlinks=followLinks):
        currentPath: Path = Path(current)
        yield WalkEntry(path=currentPath, fileNames=list(fileNames))
        if maxDepth == WALK_ALL_DEPTHS:
            continue
        depthHere: int = len(currentPath.parts) - len(root.parts)
        if depthHere >= maxDepth:
            dirNames.clear()


def stage_succeeded(results: dict[str, StageResult], enabled: bool) -> bool:
    """A stage counts as successful when skipped, or when any directory succeeded.

    Args:
        results: the stage's per-directory results.
        enabled: whether the stage ran at all.

    Returns:
        The stage's verdict.
    """
    if not enabled:
        return True
    return any(result.status for result in results.values())


class Pftree:
    """Probe, filter and process a directory tree.

    The engine is used by subclassing and overriding the three callbacks, or directly
    for its ``tree`` mapping and ``--stats`` report.

    Attributes:
        options: the TreeOptions in force.
        tree: directory key to sorted file names; filled by ``tree_construct`` and
            pruned by ``tree_filter`` and failed analyze stages.
        nodes: directory key to DirNode, in step with ``tree``.
        fileTokens: the parsed ``--fileFilter`` tokens.
        dirTokens: the parsed ``--dirFilter`` tokens.
        fileLogic: the file filter's combinator.
        dirLogic: the directory filter's combinator.
        testDelay: seconds the test analyze callback sleeps per directory.
        testType: ``0`` writes a file listing per directory, ``1`` a file count.

    Example:
        >>> tree = Pftree(TreeOptions(inputDir=Path("."), maxDepth=0, verbosity=0))
        >>> tree.run().status
        True
    """

    def __init__(self, options: TreeOptions) -> None:
        self.options: TreeOptions = options
        self.tree: dict[str, list[str]] = {}
        self.nodes: dict[str, DirNode] = {}
        self.fileTokens: list[FilterToken] = tokens_parse(options.fileFilter)
        self.dirTokens: list[FilterToken] = tokens_parse(options.dirFilter)
        self.fileLogic: FilterLogic = FilterLogic.parse(options.fileFilterLogic)
        self.dirLogic: FilterLogic = FilterLogic.parse(options.dirFilterLogic)
        self.testDelay: float = 0.0
        self.testType: int = 0
        self.startTime: float = time.perf_counter()
        self.testSpec_parse(options.test)

    def testSpec_parse(self, spec: str) -> bool:
        """Read the ``--test`` value into ``testDelay`` and ``testType``.

        Args:
            spec: ``""`` for no test, else ``"<seconds>"`` or ``"<seconds>:<type>"``.

        Returns:
            True when a test run was requested.

        Raises:
            ValueError: if the seconds or type do not parse.
        """
        if not spec:
            return False
        delayText, _, typeText = spec.partition(":")
        self.testDelay = float(delayText)
        self.testType = int(typeText) if typeText else 0
        return True

    def console_allowed(self) -> bool:
        """Whether progress and reports may go to the console.

        Returns:
            False when verbosity is 0 or the caller asked for JSON output.
        """
        if self.options.verbosity <= 0:
            return False
        return not (self.options.json or self.options.jsonStats)

    def progress_wrap(self, items: Iterable[DirNode], description: str) -> Iterable[DirNode]:
        """Wrap an iterable in a progress bar when the console is available.

        Args:
            items: the nodes about to be processed.
            description: the bar's label.

        Returns:
            The same items, possibly wrapped in tqdm.
        """
        if self.console_allowed():
            return tqdm(items, desc=description)
        return items

    def key_for(self, node: DirNode) -> str:
        """The tree key for a node under the current options."""
        return node.key(self.options.relativeDir)

    def outputPath_for(self, relPath: str) -> Path:
        """Where a directory's output goes.

        Args:
            relPath: the directory relative to ``inputDir``.

        Returns:
            ``outputDir / relPath`` with the leaf renamed by ``outputLeafDir`` when set.
            The root maps to ``outputDir`` itself.
        """
        if relPath == ".":
            return self.options.outputDir
        relative: PurePosixPath = PurePosixPath(relPath)
        if self.options.outputLeafDir:
            leaf: str = self.options.outputLeafDir % relative.name
            relative = relative.with_name(leaf)
        return self.options.outputDir / Path(relative)

    def node_build(self, entry: WalkEntry) -> DirNode:
        """Turn a walked directory into a DirNode.

        Args:
            entry: the directory and its unfiltered file names.

        Returns:
            The node, with ``inputFile`` applied to the file list.
        """
        relPath: str = PurePosixPath(entry.path.relative_to(self.options.inputDir)).as_posix()
        if relPath == "":
            relPath = "."
        names: list[str] = sorted(entry.fileNames)
        if self.options.inputFile:
            names = [name for name in names if self.options.inputFile in name]
        return DirNode(path=entry.path, relPath=relPath, files=names, outputPath=self.outputPath_for(relPath))

    def tree_probe(self) -> list[DirNode]:
        """Walk ``inputDir`` and describe every directory that holds files.

        Returns:
            One DirNode per directory with at least one (``inputFile``-matching) file.
        """
        nodes: list[DirNode] = []
        for entry in tree_walk(self.options.inputDir, self.options.maxDepth, self.options.followLinks):
            node: DirNode = self.node_build(entry)
            if node.files:
                nodes.append(node)
        logger.info("probed %d directories with files under %s", len(nodes), self.options.inputDir)
        return nodes

    def tree_construct(self, nodes: list[DirNode]) -> int:
        """Fill ``tree`` and ``nodes`` from probed directories.

        Args:
            nodes: the output of ``tree_probe``.

        Returns:
            How many directories the tree now holds.
        """
        self.tree = {}
        self.nodes = {}
        for node in nodes:
            key: str = self.key_for(node)
            self.tree[key] = list(node.files)
            self.nodes[key] = node
        return len(self.tree)

    def tree_filter(self) -> int:
        """Apply the file and directory filters to the tree in place.

        A directory whose path fails ``dirFilter``, or that keeps no files after
        ``fileFilter``, is removed.

        Returns:
            How many directories remain.
        """
        for key in list(self.nodes):
            node: DirNode = self.nodes[key]
            kept: list[str] = node.files
            if not dirPath_matches(node.relPath, self.dirTokens, self.dirLogic):
                kept = []
            elif self.fileTokens:
                kept = fileNames_filter(node.files, self.fileTokens, self.fileLogic)
            if kept:
                node.files = kept
                self.tree[key] = list(kept)
            else:
                logger.debug("no files to analyze in %s", key)
                del self.nodes[key]
                del self.tree[key]
        return len(self.tree)

    def node_drop(self, key: str) -> None:
        """Remove a directory from ``tree`` and ``nodes``."""
        self.nodes.pop(key, None)
        self.tree.pop(key, None)

    def tree_process(
        self,
        *,
        inputRead_callback: ReadCallback | None = None,
        inputAnalyze_callback: AnalyzeCallback | None = None,
        outputWrite_callback: WriteCallback | None = None,
    ) -> ProcessResult:
        """Run the read, analyze and write stages over every directory.

        Reads run first, sequentially. Analyses run next, in a thread pool when
        ``threads`` is positive. A directory whose analyze result has ``status`` False,
        or whose analyze callback raises, is dropped from the tree before writes.
        Writes run last, sequentially. Any callback may be omitted; a skipped stage
        passes an empty successful StageResult carrying the node's files forward.

        Args:
            inputRead_callback: receives the node.
            inputAnalyze_callback: receives the node and the read result.
            outputWrite_callback: receives the node and the analyze result.

        Returns:
            The run's status and per-directory results.
        """
        threaded: bool = self.options.threads > 0
        nodes: list[DirNode] = list(self.nodes.values())
        readResults: dict[str, StageResult] = {}
        analyzeResults: dict[str, StageResult] = {}
        writeResults: dict[str, StageResult] = {}

        if inputRead_callback is not None:
            for node in self.progress_wrap(nodes, "Reading tree"):
                readResults[self.key_for(node)] = inputRead_callback(node)

        if inputAnalyze_callback is not None:
            analyzeResults = self.analyze_stage(nodes, readResults, inputAnalyze_callback)
            for key, result in analyzeResults.items():
                if not result.status:
                    self.node_drop(key)
            nodes = list(self.nodes.values())

        if outputWrite_callback is not None:
            for node in self.progress_wrap(nodes, "Saving tree"):
                key = self.key_for(node)
                previous: StageResult = analyzeResults.get(key, StageResult(True, node.files))
                writeResults[key] = self.stage_guard(
                    key, "write", partial(outputWrite_callback, node, previous)
                )

        status: bool = all(
            stage_succeeded(results, enabled)
            for results, enabled in (
                (readResults, inputRead_callback is not None),
                (analyzeResults, inputAnalyze_callback is not None),
                (writeResults, outputWrite_callback is not None),
            )
        )
        return ProcessResult(
            status=status,
            threaded=threaded,
            dirsProcessed=len(self.nodes),
            filesRead=sum(result.fileCount for result in readResults.values()),
            filesAnalyzed=sum(result.fileCount for result in analyzeResults.values()),
            filesSaved=sum(result.fileCount for result in writeResults.values()),
            read=readResults,
            analyze=analyzeResults,
            write=writeResults,
        )

    def analyze_stage(
        self,
        nodes: list[DirNode],
        readResults: dict[str, StageResult],
        callback: AnalyzeCallback,
    ) -> dict[str, StageResult]:
        """Run the analyze callback over the nodes, threaded when configured.

        Args:
            nodes: the directories to analyze.
            readResults: the read stage's results, keyed like the tree.
            callback: the analyze callback.

        Returns:
            Analyze results keyed like the tree, failures included.
        """

        def node_analyze(node: DirNode) -> KeyedResult:
            key: str = self.key_for(node)
            previous: StageResult = readResults.get(key, StageResult(True, node.files))
            return KeyedResult(key, self.stage_guard(key, "analyze", partial(callback, node, previous)))

        results: dict[str, StageResult] = {}
        if self.options.threads > 0:
            with ThreadPoolExecutor(max_workers=self.options.threads) as pool:
                outcomes: Iterable[KeyedResult] = pool.map(node_analyze, nodes)
                if self.console_allowed():
                    outcomes = tqdm(outcomes, total=len(nodes), desc="Analyzing tree")
                for outcome in outcomes:
                    results[outcome.key] = outcome.result
        else:
            for node in self.progress_wrap(nodes, "Analyzing tree"):
                outcome = node_analyze(node)
                results[outcome.key] = outcome.result
        return results

    @staticmethod
    def stage_guard(key: str, stage: str, action: Callable[[], StageResult]) -> StageResult:
        """Run one callback, turning an exception into a failed result.

        Args:
            key: the directory, for the log line.
            stage: ``"analyze"`` or ``"write"``, for the log line.
            action: the callback, already bound to its arguments.

        Returns:
            The callback's result, or ``StageResult(False)`` after logging the traceback.
        """
        try:
            return action()
        except Exception:
            logger.exception("%s failed in %s", stage, key)
            return StageResult(status=False)

    def stats_compute(self) -> StatsResult:
        """Measure disk usage per directory and across the tree.

        Returns:
            The report, directories sorted by size, smallest first unless
            ``statsReverse`` is set.
        """
        perDir: list[DirStat] = []
        for key, node in self.nodes.items():
            sizeBytes: int = 0
            for name in node.files:
                filePath: Path = node.path / name
                if not filePath.is_symlink():
                    sizeBytes += filePath.stat().st_size
            perDir.append(DirStat(key, len(node.files), sizeBytes, size_format(sizeBytes)))
        perDir.sort(key=lambda stat: stat.sizeBytes, reverse=self.options.statsReverse)
        totalSize: int = sum(stat.sizeBytes for stat in perDir)
        return StatsResult(
            dirs=len(perDir),
            files=sum(stat.files for stat in perDir),
            totalSize=totalSize,
            totalSizeHuman=size_format(totalSize),
            perDir=perDir,
        )

    def inputRead_callback(self, node: DirNode) -> StageResult:
        """Test read: report the node's files without opening them.

        Args:
            node: the directory.

        Returns:
            Success with the node's files; failure if it has none.
        """
        logger.debug("reading %s: %s", node.relPath, node.files)
        return StageResult(status=bool(node.files), files=list(node.files))

    def inputAnalyze_callback(self, node: DirNode, read: StageResult) -> StageResult:
        """Test analyze: sleep ``testDelay`` seconds and pass the files through.

        Args:
            node: the directory.
            read: the read stage's result.

        Returns:
            Success with the read stage's files.
        """
        logger.debug("analyzing %s: %s", node.relPath, read.files)
        if self.testDelay > 0:
            time.sleep(self.testDelay)
        return StageResult(status=True, files=list(read.files))

    def outputSave_callback(self, node: DirNode, analyze: StageResult) -> StageResult:
        """Test write: list or count the analyzed files in the output directory.

        Args:
            node: the directory; its ``outputPath`` is created.
            analyze: the analyze stage's result.

        Returns:
            Success with the one file written.
        """
        node.outputPath.mkdir(parents=True, exist_ok=True)
        if self.testType == 0:
            outFile: Path = node.outputPath / TEST_FILELIST_NAME
            outFile.write_text("\n".join(analyze.files) + "\n")
        else:
            outFile = node.outputPath / TEST_FILECOUNT_NAME
            outFile.write_text(f"{analyze.fileCount}\n")
        logger.debug("saved %s", outFile)
        return StageResult(status=True, files=[outFile.name])

    def test_run(self) -> ProcessResult:
        """Run the three test callbacks over the tree."""
        return self.tree_process(
            inputRead_callback=self.inputRead_callback,
            inputAnalyze_callback=self.inputAnalyze_callback,
            outputWrite_callback=self.outputSave_callback,
        )

    def env_check(self) -> str:
        """Confirm the input directory exists.

        Returns:
            An empty string when all is well, else the problem.
        """
        if not self.options.inputDir.is_dir():
            return f"input directory not found: {self.options.inputDir}"
        return ""

    def stats_wanted(self) -> bool:
        """Whether any option asked for the disk-usage report."""
        opts: TreeOptions = self.options
        return opts.stats or opts.statsReverse or opts.du or opts.duf or opts.jsonStats

    def run(self) -> RunResult:
        """Probe, construct, filter, then test and report as the options ask.

        Returns:
            The run's result; ``status`` is False with ``error`` set when the input
            directory is missing, or when a test run failed.
        """
        error: str = self.env_check()
        if error:
            logger.error(error)
            return RunResult(status=False, error=error, runTime=self.runTime_get())
        self.tree_construct(self.tree_probe())
        if self.fileTokens or self.dirTokens:
            self.tree_filter()
        status: bool = True
        process: ProcessResult | None = None
        stats: StatsResult | None = None
        if self.options.test:
            process = self.test_run()
            status = status and process.status
        if self.stats_wanted():
            stats = self.stats_compute()
        return RunResult(
            status=status,
            tree=dict(self.tree),
            process=process,
            stats=stats,
            runTime=self.runTime_get(),
        )

    def runTime_get(self) -> float:
        """Seconds since this engine was constructed."""
        return time.perf_counter() - self.startTime
