"""The pftree engine: probe a directory tree, filter it, process it, report on it.

The engine turns an input directory into ``tree``, a mapping from each directory that
holds files to the names of those files, then runs up to three callbacks over every
directory: read, analyze, write. Reads and writes run sequentially; the analyze stage
can run in a thread pool. Output for a directory goes to the same relative location
under the output directory, optionally with a renamed leaf.

Key components:
    - TreeOptions: every option the engine honours
    - Pftree: the engine; subclass it and override the three ``*_callback`` methods
    - DirNode: one directory of the tree, with its input path, relative path, files
      and output path
    - StageResult: what a callback returns; ProcessResult sums a whole run
    - StatsResult / DirStat: the ``--stats`` disk-usage report

Typical usage:
    class Mine(Pftree):
        def inputAnalyze_callback(self, node: DirNode, read: StageResult) -> StageResult:
            ...
    Mine(TreeOptions(inputDir=Path("/in"), outputDir=Path("/out"))).run()

Dependencies:
    - tqdm, for progress bars on the console
    - pftree.filters, for the file and directory filters
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from functools import partial
from pathlib import Path, PurePosixPath
from typing import get_type_hints

from tqdm import tqdm

from pftree.filters import (
    FilterLogic,
    FilterToken,
    dirPath_matches,
    fileNames_filter,
    tokens_parse,
)

logger: logging.Logger = logging.getLogger("pftree")

LEAF_PLACEHOLDER: str = "%s"
WALK_ALL_DEPTHS: int = -1
TEST_FILELIST_NAME: str = "file-ls.txt"
TEST_FILECOUNT_NAME: str = "file-count.txt"
SIZE_UNITS: tuple[str, ...] = ("", "k", "M", "G", "T", "P", "E", "Z")
SIZE_STEP: float = 1024.0


@dataclass
class TreeOptions:
    """Every option the engine reads.

    Attributes:
        inputDir: the directory to walk.
        outputDir: where per-directory output lands, mirroring the input layout.
        inputFile: when set, only files whose name contains this text are listed.
        maxDepth: how many levels below ``inputDir`` to walk; ``-1`` for all.
        fileFilter: comma-separated file tokens (see ``pftree.filters``).
        fileFilterLogic: ``AND`` or ``OR`` across the file tokens.
        dirFilter: comma-separated directory tokens, tested on the relative path.
        dirFilterLogic: ``AND`` or ``OR`` across the directory tokens.
        outputLeafDir: a ``%s`` template applied to each output leaf name,
            e.g. ``"anon-%s"``.
        relativeDir: key the tree by paths relative to ``inputDir`` instead of
            the full input path.
        followLinks: follow symbolic links while walking.
        overwrite: allow callbacks to overwrite existing output; read by subclasses.
        threads: workers for the analyze stage; ``0`` runs it sequentially.
        verbosity: ``0`` silences the console; higher values show more.
        json: the caller wants machine output, so no progress bars.
        stats: compute the disk-usage report, largest directory last.
        statsReverse: compute the report, largest directory first.
        du: ``du``-style report lines.
        duf: ``du``-style report lines without console updates.
        jsonStats: the caller wants the report as JSON.
        test: run the built-in test callbacks; ``"<seconds>[:<type>]"`` where seconds
            delays each analyze call and type ``1`` writes a count instead of a listing.

    Example:
        >>> TreeOptions.fromDict({"inputDir": "/data", "threads": "4", "ignored": 1}).threads
        4
    """

    inputDir: Path = Path(".")
    outputDir: Path = Path(".")
    inputFile: str = ""
    maxDepth: int = WALK_ALL_DEPTHS
    fileFilter: str = ""
    fileFilterLogic: str = "OR"
    dirFilter: str = ""
    dirFilterLogic: str = "OR"
    outputLeafDir: str = ""
    relativeDir: bool = False
    followLinks: bool = False
    overwrite: bool = False
    threads: int = 0
    verbosity: int = 1
    json: bool = False
    stats: bool = False
    statsReverse: bool = False
    du: bool = False
    duf: bool = False
    jsonStats: bool = False
    test: str = ""

    def __post_init__(self) -> None:
        """Validate the options that have a fixed grammar.

        Raises:
            ValueError: for a bad filter logic, a malformed filter token, or an
                ``outputLeafDir`` without the ``%s`` placeholder.
        """
        FilterLogic.parse(self.fileFilterLogic)
        FilterLogic.parse(self.dirFilterLogic)
        tokens_parse(self.fileFilter)
        tokens_parse(self.dirFilter)
        if self.outputLeafDir and LEAF_PLACEHOLDER not in self.outputLeafDir:
            raise ValueError(f"outputLeafDir {self.outputLeafDir!r} needs a {LEAF_PLACEHOLDER}")

    @classmethod
    def fromDict(cls, values: dict[str, object]) -> TreeOptions:
        """Build options from a loose mapping, such as ``vars(args)``.

        Unknown keys are ignored, so a subclass's larger argument set passes through.
        Values are coerced to the field's type: paths to ``Path``, numbers to ``int``.

        Args:
            values: option name to value, strings allowed for paths and numbers.

        Returns:
            The options with every known key applied.
        """
        hints: dict[str, type] = get_type_hints(cls)
        kwargs: dict[str, object] = {}
        for item in fields(cls):
            if item.name not in values:
                continue
            raw: object = values[item.name]
            hint: type = hints[item.name]
            if hint is Path:
                kwargs[item.name] = Path(str(raw))
            elif hint is int:
                kwargs[item.name] = int(str(raw))
            elif hint is bool:
                kwargs[item.name] = bool(raw)
            else:
                kwargs[item.name] = str(raw)
        return cls(**kwargs)  # type: ignore[arg-type]  # keys are validated field names above


@dataclass
class DirNode:
    """One directory of the tree.

    Attributes:
        path: the directory as walked, under ``inputDir``.
        relPath: the directory relative to ``inputDir``, ``"."`` for the root.
        files: the bare file names kept in this directory, sorted.
        outputPath: where this directory's output goes, leaf template applied.

    Example:
        >>> DirNode(Path("/in/a"), "a", ["x.txt"], Path("/out/a")).key(relative=True)
        'a'
    """

    path: Path
    relPath: str
    files: list[str]
    outputPath: Path

    def key(self, relative: bool) -> str:
        """The tree key for this node.

        Args:
            relative: use the relative path rather than the full input path.

        Returns:
            The key string.
        """
        return self.relPath if relative else str(self.path)


@dataclass
class StageResult:
    """What one callback returns for one directory.

    Attributes:
        status: False marks the directory as failed; a failed analyze drops it.
        files: the file names this stage touched, for the counts in ProcessResult.
        payload: anything a subclass wants to carry to the next stage.

    Example:
        >>> StageResult(status=True, files=["a.txt"]).fileCount
        1
    """

    status: bool
    files: list[str] = field(default_factory=list)
    payload: dict[str, object] = field(default_factory=dict)

    @property
    def fileCount(self) -> int:
        """How many files this stage reported."""
        return len(self.files)


ReadCallback = Callable[[DirNode], StageResult]
AnalyzeCallback = Callable[[DirNode, StageResult], StageResult]
WriteCallback = Callable[[DirNode, StageResult], StageResult]


@dataclass
class ProcessResult:
    """The outcome of one ``tree_process`` run.

    Attributes:
        status: every stage that ran had at least one successful directory.
        threaded: the analyze stage used a thread pool.
        dirsProcessed: directories still in the tree when the run ended.
        filesRead / filesAnalyzed / filesSaved: sums of the stages' file counts.
        read / analyze / write: per-directory results keyed like the tree.

    Example:
        >>> ProcessResult(status=True, threaded=False).dirsProcessed
        0
    """

    status: bool
    threaded: bool
    dirsProcessed: int = 0
    filesRead: int = 0
    filesAnalyzed: int = 0
    filesSaved: int = 0
    read: dict[str, StageResult] = field(default_factory=dict)
    analyze: dict[str, StageResult] = field(default_factory=dict)
    write: dict[str, StageResult] = field(default_factory=dict)


@dataclass
class DirStat:
    """Disk usage of one directory.

    Attributes:
        path: the tree key of the directory.
        files: how many files it holds.
        sizeBytes: their total size; symbolic links are skipped.
        sizeHuman: the size as ``"12.3M"``.

    Example:
        >>> DirStat("a", 1, 2048, size_format(2048)).sizeHuman
        '2.0kB'
    """

    path: str
    files: int
    sizeBytes: int
    sizeHuman: str


@dataclass
class StatsResult:
    """The ``--stats`` report.

    Attributes:
        dirs: directories counted.
        files: files counted.
        totalSize: bytes across the tree.
        totalSizeHuman: the same, human readable.
        perDir: one DirStat per directory, sorted by size.

    Example:
        >>> StatsResult(dirs=0, files=0, totalSize=0, totalSizeHuman="0.0B").perDir
        []
    """

    dirs: int
    files: int
    totalSize: int
    totalSizeHuman: str
    perDir: list[DirStat] = field(default_factory=list)


@dataclass
class RunResult:
    """Everything ``Pftree.run`` produced.

    Attributes:
        status: the run succeeded end to end.
        tree: directory key to sorted file names, after filtering.
        process: the test run's result, when ``test`` was set.
        stats: the disk-usage report, when any stats option was set.
        error: a message when ``status`` is False for an environment reason.
        runTime: seconds from construction to the end of the run.

    Example:
        >>> RunResult(status=True, tree={}).runTime
        0.0
    """

    status: bool
    tree: dict[str, list[str]] = field(default_factory=dict)
    process: ProcessResult | None = None
    stats: StatsResult | None = None
    error: str = ""
    runTime: float = 0.0

    def toDict(self) -> dict[str, object]:
        """A JSON-ready view of the result.

        Returns:
            Nested plain dicts and lists; dataclasses expanded.
        """
        return asdict(self)


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


def tree_walk(root: Path, maxDepth: int, followLinks: bool) -> Iterator[tuple[Path, list[str]]]:
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
        yield currentPath, fileNames
        if maxDepth == WALK_ALL_DEPTHS:
            continue
        depthHere: int = len(currentPath.parts) - len(root.parts)
        if depthHere >= maxDepth:
            dirNames.clear()


class Pftree:
    """Probe, filter and process a directory tree.

    The engine is used by subclassing and overriding the three callbacks, or directly
    for its ``tree`` mapping and ``--stats`` report.

    Attributes:
        options: the TreeOptions in force.
        tree: directory key to sorted file names; filled by ``tree_construct`` and
            pruned by ``tree_filter`` and failed analyze stages.
        nodes: directory key to DirNode, in step with ``tree``.
        fileTokens / dirTokens: the parsed filters.
        fileLogic / dirLogic: their combinators.
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

    def node_build(self, path: Path, fileNames: list[str]) -> DirNode:
        """Turn a walked directory into a DirNode.

        Args:
            path: the directory as walked.
            fileNames: its file names, unfiltered.

        Returns:
            The node, with ``inputFile`` applied to the file list.
        """
        relPath: str = PurePosixPath(path.relative_to(self.options.inputDir)).as_posix()
        if relPath == "":
            relPath = "."
        names: list[str] = sorted(fileNames)
        if self.options.inputFile:
            names = [name for name in names if self.options.inputFile in name]
        return DirNode(path=path, relPath=relPath, files=names, outputPath=self.outputPath_for(relPath))

    def tree_probe(self) -> list[DirNode]:
        """Walk ``inputDir`` and describe every directory that holds files.

        Returns:
            One DirNode per directory with at least one (``inputFile``-matching) file.
        """
        nodes: list[DirNode] = []
        for path, fileNames in tree_walk(
            self.options.inputDir, self.options.maxDepth, self.options.followLinks
        ):
            node: DirNode = self.node_build(path, fileNames)
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

        def node_analyze(node: DirNode) -> tuple[str, StageResult]:
            key: str = self.key_for(node)
            previous: StageResult = readResults.get(key, StageResult(True, node.files))
            return key, self.stage_guard(key, "analyze", partial(callback, node, previous))

        results: dict[str, StageResult] = {}
        if self.options.threads > 0:
            with ThreadPoolExecutor(max_workers=self.options.threads) as pool:
                outcomes: Iterable[tuple[str, StageResult]] = pool.map(node_analyze, nodes)
                if self.console_allowed():
                    outcomes = tqdm(outcomes, total=len(nodes), desc="Analyzing tree")
                for key, result in outcomes:
                    results[key] = result
        else:
            for node in self.progress_wrap(nodes, "Analyzing tree"):
                key, result = node_analyze(node)
                results[key] = result
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
            directory is missing, or when a test run or report failed.
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
