"""The pftree engine: probe a directory tree, filter it, process it, report on it.

The engine turns an input directory into ``tree``, a mapping from each directory that
holds files to the names of those files, then runs up to three callbacks over every
directory: read, analyze, write. Reads and writes run sequentially; the analyze stage
can run in a thread pool. Output for a directory goes to the same relative location
under the output directory, optionally with a renamed leaf.

Key components:
    - Pftree: the engine; subclass it and override the three ``*_callback`` methods
    - TreeScanner: one ``scandir`` pass yielding entries top-down and ``du`` usage
      post-order, every entry stat-ed once
    - tree_walk: the scanner's entries as an iterator
    - du_humanize, kilobytes_roundUp: ``du``'s number formats
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
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
    DirUsage,
    DuLine,
    DuReport,
    FileSize,
    KeyedResult,
    ProcessResult,
    RunResult,
    StageResult,
    StatsResult,
    TreeOptions,
    TreeScan,
    WalkEntry,
)

logger: logging.Logger = logging.getLogger("pftree")

TEST_FILELIST_NAME: str = "file-ls.txt"
TEST_FILECOUNT_NAME: str = "file-count.txt"
SIZE_UNITS: tuple[str, ...] = ("", "k", "M", "G", "T", "P", "E", "Z")
SIZE_STEP: float = 1024.0
BLOCK_BYTES: int = 512
KILOBYTE: int = 1024
DU_UNITS: tuple[str, ...] = ("", "K", "M", "G", "T", "P", "E")

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


def fileSize_fromStat(status: os.stat_result, isLink: bool = False) -> FileSize:
    """Read the two sizes out of a stat result.

    Args:
        status: an ``lstat`` result.
        isLink: the entry is a symbolic link.

    Returns:
        The apparent and disk sizes.
    """
    return FileSize(apparentBytes=status.st_size, diskBytes=status.st_blocks * BLOCK_BYTES, isLink=isLink)


def entry_pointsToDir(entry: os.DirEntry[str]) -> bool:
    """Whether an entry is, or links to, a directory.

    Args:
        entry: a scandir entry.

    Returns:
        True for a directory or a symbolic link to one; False for anything else,
        including a link that cannot be resolved (a loop, a dangling target).
    """
    try:
        return entry.is_dir()
    except OSError:
        return False


def gil_isEnabled() -> bool:
    """Whether this interpreter runs with the global interpreter lock.

    Returns:
        True on an ordinary build, or on any Python before 3.13; False on a
        free-threaded build (``python3.13t`` and later) where threads run in parallel.
    """
    probe: Callable[[], bool] | None = getattr(sys, "_is_gil_enabled", None)
    if probe is None:
        return True
    return bool(probe())


class InodeLedger:
    """The inodes a scan has already counted, shared by every thread of the scan.

    Attributes:
        files: ``(st_dev, st_ino)`` of files already charged to a directory.
        dirs: ``(st_dev, st_ino)`` of directories entered through symbolic links.
        lock: guards both sets; uncontended in a single-threaded scan.

    Example:
        >>> ledger = InodeLedger()
        >>> ledger.file_claim((1, 2)), ledger.file_claim((1, 2))
        (True, False)
    """

    def __init__(self) -> None:
        self.files: set[tuple[int, int]] = set()
        self.dirs: set[tuple[int, int]] = set()
        self.lock: threading.Lock = threading.Lock()

    def file_claim(self, inode: tuple[int, int]) -> bool:
        """Claim a file inode for the caller.

        Args:
            inode: ``(st_dev, st_ino)``.

        Returns:
            True the first time the inode is claimed, False after.
        """
        with self.lock:
            if inode in self.files:
                return False
            self.files.add(inode)
            return True

    def dir_claim(self, inode: tuple[int, int]) -> bool:
        """Claim a directory inode reached through a link.

        Args:
            inode: ``(st_dev, st_ino)``.

        Returns:
            True the first time the inode is claimed, False after.
        """
        with self.lock:
            if inode in self.dirs:
                return False
            self.dirs.add(inode)
            return True


SubdirHandler = Callable[[Path, FileSize, int], DirUsage | None]


@dataclass
class SplitJob:
    """A directory at the split level, waiting for a worker.

    Attributes:
        path: the directory.
        size: its own sizes, from the parent's ``lstat``.
        depth: its depth below the root.
        entryIndex: where in ``entries`` its subtree's entries belong.
        usageIndex: where in ``usage`` its subtree's usage lines belong.

    Example:
        >>> SplitJob(Path("/in/a"), FileSize(0, 0), 1, 1, 0).depth
        1
    """

    path: Path
    size: FileSize
    depth: int
    entryIndex: int
    usageIndex: int


class TreeScanner:
    """One pass over a directory tree: entries top-down, usage post-order.

    Every entry is ``lstat``-ed once, through ``os.scandir``, during the walk; nothing
    is revisited. Hard-linked files count once towards usage, as ``du`` counts them.
    With ``threads`` above one, each directory at ``splitDepth`` is scanned by its own
    worker; that pays only on a free-threaded Python (see ``gil_isEnabled``), and the
    directory a hard link shared between subtrees is charged to then depends on timing.

    Attributes:
        root: where the scan starts.
        maxDepth: levels below ``root`` to descend; ``-1`` for all, ``0`` for root only.
        followLinks: descend into symbolic links to directories, each target once.
        threads: workers for the directories at the split level; ``1`` scans in order.
        splitDepth: the level whose directories become the workers' jobs; clamped to
            ``maxDepth``.
        recordFiles: keep each directory's file names and sizes in its WalkEntry.
            False when only the usage lines are wanted, which skips the per-file
            records and their cost.
        entries: the directories in scan order, filled by ``scan``.
        usage: the directories in post-order with the root last, filled by ``scan``.
        ledger: the inodes counted so far, shared with the workers.

    Example:
        >>> scan = TreeScanner(Path("."), maxDepth=0, followLinks=False).scan()
        >>> scan.usage[-1].path
        PosixPath('.')
    """

    def __init__(
        self,
        root: Path,
        maxDepth: int,
        followLinks: bool,
        threads: int = 1,
        splitDepth: int = 1,
        ledger: InodeLedger | None = None,
        recordFiles: bool = True,
    ) -> None:
        self.root: Path = root
        self.maxDepth: int = maxDepth
        self.followLinks: bool = followLinks
        self.threads: int = threads
        self.recordFiles: bool = recordFiles
        self.splitDepth: int = splitDepth if maxDepth == WALK_ALL_DEPTHS else min(splitDepth, maxDepth)
        self.entries: list[WalkEntry] = []
        self.usage: list[DirUsage] = []
        self.ledger: InodeLedger = ledger if ledger is not None else InodeLedger()

    def scan(self) -> TreeScan:
        """Run the scan, threaded across the first level when ``threads`` allows.

        Returns:
            The entries and the usage lines.
        """
        rootStatus: os.stat_result = os.lstat(self.root)
        rootSize: FileSize = fileSize_fromStat(rootStatus)
        if self.followLinks:
            self.ledger.dir_claim((rootStatus.st_dev, rootStatus.st_ino))
        if self.threads > 1 and self.splitDepth >= 1:
            self.scan_threaded(rootSize)
        else:
            self.dir_scan(self.root, rootSize, depth=0, onSubdir=self.subdir_scan)
        return TreeScan(entries=self.entries, usage=self.usage)

    def scan_threaded(self, rootSize: FileSize) -> None:
        """Scan down to ``splitDepth`` in this thread, each directory there in a worker.

        Directories above the split level, and their files, are scanned here in
        directory order; every directory at the split level becomes one job. The
        workers' entries and usage lines are spliced back where the sequential scan
        would have put them, and their totals added to every ancestor, so the output
        is the sequential scan's.

        Args:
            rootSize: the root directory's own sizes.
        """
        jobs: list[SplitJob] = []

        def subdir_handle(path: Path, size: FileSize, depth: int) -> DirUsage | None:
            if depth < self.splitDepth:
                return self.dir_scan(path, size, depth, onSubdir=subdir_handle)
            jobs.append(SplitJob(path, size, depth, len(self.entries), len(self.usage)))
            return None

        self.dir_scan(self.root, rootSize, depth=0, onSubdir=subdir_handle)
        usageByPath: dict[Path, DirUsage] = {usage.path: usage for usage in self.usage}

        def job_run(job: SplitJob) -> TreeScanner:
            worker: TreeScanner = TreeScanner(
                self.root, self.maxDepth, self.followLinks, ledger=self.ledger, recordFiles=self.recordFiles
            )
            worker.dir_scan(job.path, job.size, job.depth, onSubdir=worker.subdir_scan)
            return worker

        with ThreadPoolExecutor(max_workers=self.threads) as pool:
            workers: list[TreeScanner] = list(pool.map(job_run, jobs))
        for job, worker in zip(reversed(jobs), reversed(workers), strict=True):
            self.entries[job.entryIndex : job.entryIndex] = worker.entries
            self.usage[job.usageIndex : job.usageIndex] = worker.usage
            subtree: DirUsage = worker.usage[-1]
            for ancestor in self.ancestors_of(job.path):
                usageByPath[ancestor].diskBytes += subtree.diskBytes
                usageByPath[ancestor].apparentBytes += subtree.apparentBytes

    def ancestors_of(self, path: Path) -> list[Path]:
        """The directories from a job's parent up to the root, as walked.

        Args:
            path: a directory below the root.

        Returns:
            Its parent, grandparent and so on, ending with ``root``.
        """
        ancestors: list[Path] = []
        current: Path = path
        while current != self.root:
            current = current.parent
            ancestors.append(current)
        return ancestors

    def subdir_scan(self, path: Path, size: FileSize, depth: int) -> DirUsage:
        """The sequential subdirectory handler: descend at once."""
        return self.dir_scan(path, size, depth, onSubdir=self.subdir_scan)

    def dir_scan(self, path: Path, dirSize: FileSize, depth: int, onSubdir: SubdirHandler) -> DirUsage:
        """Scan one directory and, within the depth bound, everything below it.

        Entries are handled in directory order; a subdirectory is handed to
        ``onSubdir`` the moment it is met, which is the order ``du`` charges hard
        links in when the handler descends at once.

        Args:
            path: the directory.
            dirSize: its own sizes, from the parent's ``lstat`` of it.
            depth: how many levels below the root it sits.
            onSubdir: called for each subdirectory to descend into; returns its
                cumulative usage, or None when it will be scanned later.

        Returns:
            The directory's cumulative usage; also appended to ``usage`` after the
            usage of every subdirectory the handler scanned.
        """
        fileNames: list[str] = []
        fileSizes: dict[str, FileSize] = {}
        diskBytes: int = dirSize.diskBytes
        apparentBytes: int = 0  # du --apparent-size gives a directory itself no size
        descend: bool = self.maxDepth == WALK_ALL_DEPTHS or depth < self.maxDepth
        self.entries.append(WalkEntry(path=path, fileNames=fileNames, fileSizes=fileSizes, dirSize=dirSize))
        try:
            with os.scandir(path) as listing:
                for entry in listing:
                    try:
                        status: os.stat_result = entry.stat(follow_symlinks=False)
                        isLink: bool = entry.is_symlink()
                        isDir: bool = entry.is_dir(follow_symlinks=self.followLinks)
                    except OSError as err:
                        logger.warning("cannot stat %s: %s", entry.path, err.strerror)
                        continue
                    if isDir and self.dir_enterable(entry, isLink):
                        if descend:
                            if isLink:
                                status, isLink = self.link_resolve(entry, status)
                            below: DirUsage | None = onSubdir(
                                Path(entry.path), fileSize_fromStat(status, isLink), depth + 1
                            )
                            if below is not None:
                                diskBytes += below.diskBytes
                                apparentBytes += below.apparentBytes
                        continue
                    if isLink and self.followLinks:
                        status, isLink = self.link_resolve(entry, status)
                    if self.recordFiles:
                        fileSizes[entry.name] = fileSize_fromStat(status, isLink)
                        if not entry_pointsToDir(entry):
                            fileNames.append(entry.name)
                    if (
                        not isLink
                        and (status.st_nlink > 1 or self.followLinks)
                        and not self.ledger.file_claim((status.st_dev, status.st_ino))
                    ):
                        continue
                    diskBytes += status.st_blocks * BLOCK_BYTES
                    apparentBytes += status.st_size
        except OSError as err:
            logger.warning("cannot read %s: %s", path, err.strerror)
        usage: DirUsage = DirUsage(path=path, diskBytes=diskBytes, apparentBytes=apparentBytes)
        self.usage.append(usage)
        return usage

    @staticmethod
    def link_resolve(entry: os.DirEntry[str], linkStatus: os.stat_result) -> tuple[os.stat_result, bool]:
        """Under ``followLinks``, a symbolic link to a file counts as its target.

        Args:
            entry: the link's scandir entry.
            linkStatus: the link's own ``lstat``, used when the target is unreachable.

        Returns:
            The status to size the entry by and whether it still counts as a link:
            the target's status and False, or the link's own and True when the target
            cannot be stat-ed (a dangling link), as ``du -L`` does.
        """
        try:
            return entry.stat(follow_symlinks=True), False
        except OSError:
            return linkStatus, True

    def dir_enterable(self, entry: os.DirEntry[str], isLink: bool) -> bool:
        """Whether a directory entry should be descended into.

        A real directory always is. A symbolic link to a directory is entered only
        with ``followLinks``, and only the first time its target is met.

        Args:
            entry: the scandir entry.
            isLink: the entry is a symbolic link.

        Returns:
            True to descend.
        """
        if not isLink:
            return True
        if not self.followLinks:
            return False
        try:
            target: os.stat_result = entry.stat(follow_symlinks=True)
        except OSError as err:
            logger.warning("cannot follow %s: %s", entry.path, err.strerror)
            return False
        return self.ledger.dir_claim((target.st_dev, target.st_ino))


def tree_walk(root: Path, maxDepth: int, followLinks: bool) -> Iterator[WalkEntry]:
    """Walk a directory to a bounded depth.

    Args:
        root: where to start.
        maxDepth: levels below ``root`` to descend; ``-1`` for all, ``0`` for root only.
        followLinks: follow symbolic links to directories.

    Yields:
        Each directory and its file names, root first, in scan order.
    """
    yield from TreeScanner(root, maxDepth, followLinks).scan().entries


def kilobytes_roundUp(byteCount: int) -> int:
    """Bytes to 1024-byte units, rounded up as ``du -k`` rounds.

    Args:
        byteCount: a size in bytes.

    Returns:
        The size in kilobytes.
    """
    return -(-byteCount // KILOBYTE)


def du_humanize(byteCount: int) -> str:
    """Render a size the way ``du -h`` does.

    Args:
        byteCount: a size in bytes.

    Returns:
        ``"0"``, ``"4.0K"``, ``"12K"``, ``"1.5M"``, ``"3.0G"``: one decimal below 10,
        none above, rounded up, in powers of 1024.
    """
    if byteCount == 0:
        return "0"
    unitIndex: int = 0
    scale: int = 1
    while byteCount >= scale * KILOBYTE and unitIndex < len(DU_UNITS) - 1:
        scale *= KILOBYTE
        unitIndex += 1
    unit: str = DU_UNITS[unitIndex]
    if unitIndex == 0:
        return f"{byteCount}{unit}"
    tenths: int = -(-byteCount * 10 // scale)
    if tenths < 100:
        return f"{tenths // 10}.{tenths % 10}{unit}"
    return f"{-(-byteCount // scale)}{unit}"


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
        usage: every scanned directory's cumulative usage, post-order, root last.
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
        self.usage: list[DirUsage] = []
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
        return DirNode(
            path=entry.path,
            relPath=relPath,
            files=names,
            outputPath=self.outputPath_for(relPath),
            fileSizes=entry.fileSizes,
        )

    def scanner_build(self, recordFiles: bool = True) -> TreeScanner:
        """A scanner over ``inputDir`` configured from the options.

        Args:
            recordFiles: keep per-file records (see TreeScanner).

        Returns:
            The scanner, not yet run.
        """
        return TreeScanner(
            self.options.inputDir,
            self.options.maxDepth,
            self.options.followLinks,
            self.scanThreads_get(),
            self.options.threadsSplit,
            recordFiles=recordFiles,
        )

    def usage_probe(self) -> list[DirUsage]:
        """Scan ``inputDir`` for the ``du`` report alone, without per-file records.

        Returns:
            The usage lines, also kept in ``usage``; ``tree`` and ``nodes`` are left
            empty.
        """
        self.usage = self.scanner_build(recordFiles=False).scan().usage
        return self.usage

    def tree_probe(self) -> list[DirNode]:
        """Scan ``inputDir`` once and describe every directory that holds files.

        The scan's usage lines are kept in ``usage`` for the ``du`` report.

        Returns:
            One DirNode per directory with at least one (``inputFile``-matching) file.
        """
        scan: TreeScan = self.scanner_build().scan()
        self.usage = scan.usage
        nodes: list[DirNode] = []
        for entry in scan.entries:
            node: DirNode = self.node_build(entry)
            if node.files:
                nodes.append(node)
        logger.info("probed %d directories with files under %s", len(nodes), self.options.inputDir)
        return nodes

    def scanThreads_get(self) -> int:
        """How many threads the scan may use.

        ``threads`` applies to the scan only on a free-threaded Python; under the
        global interpreter lock a threaded scan is slower than a sequential one, so
        the request is logged and the scan runs in one thread.

        Returns:
            ``options.threads`` on a free-threaded build, else 1.
        """
        if self.options.threads <= 1:
            return 1
        if gil_isEnabled():
            logger.warning(
                "--threads %d: the scan runs single-threaded under the GIL; "
                "use a free-threaded Python (3.13t or later)",
                self.options.threads,
            )
            return 1
        return self.options.threads

    def du_compute(self) -> DuReport:
        """The ``du`` report from the last scan.

        Returns:
            One line per directory scanned, post-order with the root last, in
            kilobytes of disk blocks, or of apparent size with ``apparentSize``.
        """
        apparent: bool = self.options.apparentSize
        lines: list[DuLine] = []
        for usage in self.usage:
            byteCount: int = usage.apparentBytes if apparent else usage.diskBytes
            lines.append(
                DuLine(kilobytes=kilobytes_roundUp(byteCount), byteCount=byteCount, path=str(usage.path))
            )
        return DuReport(lines=lines, apparent=apparent)

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
                size: FileSize | None = node.fileSizes.get(name)
                if size is not None and not size.isLink:
                    sizeBytes += size.apparentBytes
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
        """Whether any option asked for the per-directory report."""
        opts: TreeOptions = self.options
        return opts.stats or opts.statsReverse or opts.jsonStats

    def du_wanted(self) -> bool:
        """Whether ``--du`` or ``--duf`` asked for the ``du`` report."""
        return self.options.du or self.options.duf

    def duOnly_wanted(self) -> bool:
        """Whether the run needs nothing but the ``du`` report.

        Returns:
            True when ``du`` or ``duf`` is set and no tree, stats, test run or JSON
            output is asked for, so the scan can skip the per-file records.
        """
        opts: TreeOptions = self.options
        return self.du_wanted() and not (self.stats_wanted() or opts.test or opts.json)

    def run(self) -> RunResult:
        """Probe, construct, filter, then test and report as the options ask.

        A run that wants only the ``du`` report scans without per-file records and
        returns an empty ``tree``.

        Returns:
            The run's result; ``status`` is False with ``error`` set when the input
            directory is missing, or when a test run failed.
        """
        error: str = self.env_check()
        if error:
            logger.error(error)
            return RunResult(status=False, error=error, runTime=self.runTime_get())
        if self.duOnly_wanted():
            self.usage_probe()
            return RunResult(status=True, du=self.du_compute(), runTime=self.runTime_get())
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
        du: DuReport | None = self.du_compute() if self.du_wanted() else None
        return RunResult(
            status=status,
            tree=dict(self.tree),
            process=process,
            stats=stats,
            du=du,
            runTime=self.runTime_get(),
        )

    def runTime_get(self) -> float:
        """Seconds since this engine was constructed."""
        return time.perf_counter() - self.startTime
