"""The data models of pftree: options in, typed results out.

Every non-primitive value that crosses a method boundary in pftree is one of these
dataclasses. ``TreeOptions`` carries the configuration; ``DirNode`` describes one
directory of the tree; ``StageResult`` is what a callback returns; the remaining
models aggregate a processing run, a disk-usage report and a whole ``run``.

Key components:
    - TreeOptions: every option the engine honours, with ``fromDict`` for argparse
    - WalkEntry: one directory and its file names as the walk yields them
    - DirNode: a directory with its relative path, kept files and output path
    - StageResult: a callback's verdict, touched files and payload
    - KeyedResult: a StageResult paired with its tree key, from the thread pool
    - ProcessResult: the read / analyze / write run over the tree
    - DirStat / StatsResult: the disk-usage report
    - RunResult: everything ``Pftree.run`` produced, with ``toDict`` for JSON

Typical usage:
    options = TreeOptions.fromDict(vars(args))
    result: RunResult = Pftree(options).run()
    print(json.dumps(result.toDict()))

Dependencies:
    - pftree.filters, to validate the filter options at construction
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import get_type_hints

from pftree.filters import FilterLogic, tokens_parse

LEAF_PLACEHOLDER: str = "%s"
WALK_ALL_DEPTHS: int = -1


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
        threads: workers for the scan (honoured on a free-threaded Python only) and
            for the analyze stage; ``0`` runs both sequentially.
        threadsSplit: the directory level whose directories the scan's threads take
            as jobs; ``1`` is the first level below ``inputDir``.
        verbosity: ``0`` silences the console; higher values show more.
        json: the caller wants machine output, so no progress bars.
        stats: compute the per-directory report, largest directory last.
        statsReverse: compute the report, largest directory first.
        du: emit what ``du -k`` prints: cumulative kilobytes per directory.
        duf: emit what ``du -h`` prints: the same, human readable.
        apparentSize: ``du`` counts ``st_size`` instead of disk blocks, like
            ``du --apparent-size``.
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
    threadsSplit: int = 1
    verbosity: int = 1
    json: bool = False
    stats: bool = False
    statsReverse: bool = False
    du: bool = False
    duf: bool = False
    apparentSize: bool = False
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


@dataclass(slots=True)
class FileSize:
    """The two sizes of one filesystem entry, from a single ``lstat``.

    Attributes:
        apparentBytes: ``st_size``, what ``du --apparent-size`` counts.
        diskBytes: ``st_blocks * 512``, what ``du`` counts.
        isLink: the entry is a symbolic link; the sizes are the link's own.

    Example:
        >>> FileSize(apparentBytes=10, diskBytes=4096).diskBytes
        4096
    """

    apparentBytes: int
    diskBytes: int
    isLink: bool = False


@dataclass
class WalkEntry:
    """One directory as the scan yields it.

    Attributes:
        path: the directory.
        fileNames: the names of the entries directly inside it that are not
            directories, unfiltered.
        fileSizes: each of those names to its sizes.
        dirSize: the directory's own sizes.

    Example:
        >>> WalkEntry(Path("/in/a"), ["x.txt"], {}, FileSize(0, 0)).fileNames
        ['x.txt']
    """

    path: Path
    fileNames: list[str]
    fileSizes: dict[str, FileSize]
    dirSize: FileSize


@dataclass
class DirUsage:
    """The cumulative usage of one directory, as ``du`` reports it.

    Attributes:
        path: the directory, as walked.
        diskBytes: ``st_blocks * 512`` summed over the directory, its files and
            everything below it, hard links counted once.
        apparentBytes: ``st_size`` summed the same way.

    Example:
        >>> DirUsage(Path("/in"), diskBytes=8192, apparentBytes=100).diskBytes
        8192
    """

    path: Path
    diskBytes: int
    apparentBytes: int


@dataclass
class TreeScan:
    """Everything one pass over the input directory produced.

    Attributes:
        entries: one WalkEntry per directory, top-down in scan order.
        usage: one DirUsage per directory, post-order with the root last, which is
            the order ``du`` prints.

    Example:
        >>> TreeScan(entries=[], usage=[]).entries
        []
    """

    entries: list[WalkEntry]
    usage: list[DirUsage]


@dataclass
class DuLine:
    """One line of the ``du`` report.

    Attributes:
        kilobytes: the directory's cumulative size in 1024-byte units, rounded up,
            what ``du -k`` prints.
        byteCount: the same size in bytes, what ``du -h`` formats from.
        path: the directory, as walked.

    Example:
        >>> DuLine(kilobytes=4, byteCount=4096, path="/in/a").kilobytes
        4
    """

    kilobytes: int
    byteCount: int
    path: str


@dataclass
class DuReport:
    """The ``--du`` report: what ``du -k`` prints, in its order.

    Attributes:
        lines: one DuLine per directory, post-order, root last.
        apparent: the sizes are apparent (``st_size``) rather than disk blocks.

    Example:
        >>> DuReport(lines=[], apparent=False).lines
        []
    """

    lines: list[DuLine]
    apparent: bool


@dataclass
class DirNode:
    """One directory of the tree.

    Attributes:
        path: the directory as walked, under ``inputDir``.
        relPath: the directory relative to ``inputDir``, ``"."`` for the root.
        files: the bare file names kept in this directory, sorted.
        outputPath: where this directory's output goes, leaf template applied.
        fileSizes: every file name the scan saw in this directory to its sizes,
            including names later removed by a filter.

    Example:
        >>> DirNode(Path("/in/a"), "a", ["x.txt"], Path("/out/a")).key(relative=True)
        'a'
    """

    path: Path
    relPath: str
    files: list[str]
    outputPath: Path
    fileSizes: dict[str, FileSize] = field(default_factory=dict)

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


@dataclass
class KeyedResult:
    """A stage result paired with the tree key it belongs to.

    The analyze stage hands these back from its worker pool so results can be
    collected in any order.

    Attributes:
        key: the directory's tree key.
        result: the callback's result for that directory.

    Example:
        >>> KeyedResult("a", StageResult(True)).key
        'a'
    """

    key: str
    result: StageResult


@dataclass
class ProcessResult:
    """The outcome of one ``tree_process`` run.

    Attributes:
        status: every stage that ran had at least one successful directory.
        threaded: the analyze stage used a thread pool.
        dirsProcessed: directories still in the tree when the run ended.
        filesRead: sum of the read stage's file counts.
        filesAnalyzed: sum of the analyze stage's file counts.
        filesSaved: sum of the write stage's file counts.
        read: per-directory read results keyed like the tree.
        analyze: per-directory analyze results, failures included.
        write: per-directory write results.

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
        sizeHuman: the size as ``"12.3MB"``.

    Example:
        >>> DirStat("a", 1, 2048, "2.0kB").sizeHuman
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
        stats: the per-directory report, when a stats option was set.
        du: the ``du`` report, when ``du`` or ``duf`` was set.
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
    du: DuReport | None = None
    error: str = ""
    runTime: float = 0.0

    def toDict(self) -> dict[str, object]:
        """A JSON-ready view of the result.

        Returns:
            Nested plain dicts and lists; dataclasses expanded.
        """
        return asdict(self)
