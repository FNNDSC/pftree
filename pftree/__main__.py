"""The ``pftree`` command line and its manual.

Walk a directory, print its tree as JSON, or report disk usage per directory in the
style of ``du``. ``--help`` is argparse's quick reference; ``--man`` prints the full
manual below, ``--synopsis`` its first page.

The parser and the manual are both built from parts (I/O, core, self) so that a
package built on pftree, such as pfdo, can reuse the core and self parts and supply
its own I/O arguments and its own manual pages around them.

Key components:
    - BANNER and the CLI_* / ARGS_SYNOPSIS_* texts: the manual's building blocks
    - synopsis: assembles the manual, short or full
    - parserIO_build / parserCore_build / parserSelf_build: the argument groups
    - parser_build: the assembled parser, standalone or ChRIS-DS style
    - options_fromArgs: Namespace to TreeOptions
    - stats_render: the text report
    - main: the entry point, honouring an explicit ``argv``

Typical usage:
    pftree --inputDir /data --stats
    pftree --inputDir /data --fileFilter 'glob:*.dcm' --json
    pftree --man

Dependencies:
    - pftree.tree, pftree.models
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from pftree import __version__
from pftree.manual import Manual, ManualAction, manual_attach
from pftree.models import DuReport, RunResult, StatsResult, TreeOptions
from pftree.tree import Pftree, du_humanize

BANNER_TITLE: str = "Path-File tree structure"

BANNER_ART: str = r"""

        __ _
       / _| |
 _ __ | |_| |_ _ __ ___  ___
| '_ \|  _| __| '__/ _ \/ _ \
| |_) | | | |_| | |  __/  __/
| .__/|_|  \__|_|  \___|\___|
| |
|_|

"""

BANNER_TEXT: str = """
                            Path-File tree structure

        Recursively walk down an input directory tree and create a  dictionary
        representation of the path structure. Each tree "key" corresponds to a
        directory path and the key  "value" is a list  of files in  that corre-
        sponding directory in the filesystem.

                             -- version {version} --

        The main purpose of this module, other than probing a filesystem tree,
        is to provide some base methods to a caller that:

            * read input files in an input directory
            * analyze these files
            * save resultant files in an output directory

        Callers can override these methods for specific needs/workflows.  This
        module can orchestrate the flow of these core methods in either single
        or multiple threaded modes.

"""

CLI_IO_CORE: str = """
        --inputDir <inputDir>                                                   \\
        [--outputDir <outputDir>]                                               \\"""

CLI_DS_IO: str = """
        <inputDir>                                                              \\
        <outputDir>                                                             \\"""

CLI_CORE: str = """
        [--fileFilter <token1,token2,...>]                                      \\
        [--fileFilterLogic AND|OR]                                              \\
        [--dirFilter <token1,token2,...>]                                       \\
        [--dirFilterLogic AND|OR]                                               \\
        [--maxDepth <dirDepth>]                                                 \\
        [--inputFile <inputFile>]                                               \\
        [--relativeDir]                                                         \\
        [--overwrite]                                                           \\
        [--followLinks]                                                         \\
        [--outputLeafDir <outputLeafDirFormat>]                                 \\
        [--printElapsedTime]                                                    \\
        [--man]                                                                 \\
        [--synopsis]                                                            \\
        [--verbosity <verbosity>]                                               \\
        [--version]                                                             \\
        [--threads <numThreads>]                                                \\
        [--threadsSplit <level>]                                                \\
        [--json]
"""

CLI_SELF: str = """
        [--stats | --statsReverse | --jsonStats]                                \\
        [--du | --duf] [--apparentSize]                                         \\
        [--test <analysisDelayLength[:<type>]>]                                 \\"""

ARGS_SYNOPSIS_SELF: str = """
        [--stats | --statsReverse]
        If specified, report on every directory in the tree that holds files:
        how many files it holds and their apparent size, one line per
        directory sorted by size (largest last, or first with
        --statsReverse), followed by the totals. Sizes count only the files
        kept by any --fileFilter / --dirFilter, so

            --fileFilter 'glob:*.nii.gz' --stats

        answers "where are the NIfTI files, and how big are they".

        [--jsonStats]
        If specified, do a JSON dump of the stats.

        [--du | --duf]
        Print what GNU du prints. --du is 'du -k': one line per directory,
        the cumulative size in kilobytes, a tab, the path; every directory
        under the <inputDir> appears, children before parents, the
        <inputDir> last. The output is byte-for-byte what du would print, so
        it pipes into tools that read du:

            pftree --du --inputDir /data | xdu
            pftree --du --inputDir /data | sort -n | tail

        --duf is 'du -h': the same lines with human-readable sizes. Sizes are
        disk blocks (st_blocks), hard links are counted once, symbolic links
        count as themselves, and filters do not apply (du has none). With
        --maxDepth the deeper levels are not scanned, so the totals exclude
        them. With a threaded scan, the directory a hard link shared between
        subtrees is charged to can differ from du's choice; the totals do
        not.

        [--apparentSize]
        With --du or --duf, count each entry's st_size instead of its disk
        blocks, as 'du --apparent-size' does.

        [--test <analysisDelayLength[:<type>]>]
        If specified, perform a test/dummy run through the

            - read
            - analyze
            - write

        callbacks. The <analysisDelayLength> denotes time (in seconds)
        to delay in the analysis loop -- useful for testing threading
        performance.

        An optional [:<type>] can be specified.

            :0  - write the list of files to each outputdir, i.e. a simple
                  'ls' analog (file-ls.txt)
            :1  - write only the number of files analyzed to each outputdir,
                  i.e. a summary (file-count.txt)

        For large trees, ':0' can take a significantly longer time than
        ':1'.
"""

ARGS_SYNOPSIS_IO: str = """

        --inputDir <inputDir>
        The input tree. The <inputDir> and every directory under it, at any
        depth, are candidates for processing.

        [--outputDir <outputDir>]
        The output tree. Each directory processed in the <inputDir> has a
        directory at the same relative path here, which receives its
        results.
"""

ARGS_SYNOPSIS_DS: str = """

        <inputDir>
        Required, first positional argument: the input tree. The <inputDir>
        and every directory under it, at any depth, are candidates for
        processing.

        <outputDir>
        Required, second positional argument: the output tree. Each
        directory processed in the <inputDir> has a directory at the same
        relative path here, which receives its results.
"""

ARGS_SYNOPSIS_CORE: str = """

        [--maxDepth <dirDepth>]
        How many levels below the <inputDir> to descend: 0 is the <inputDir>
        alone, 1 adds its subdirectories, and so on. The default, -1, walks
        the whole tree.

        [--relativeDir]
        Name directories by their path relative to the <inputDir> in the
        --json record, sub-01 rather than /incoming/sub-01. It changes
        nothing else.

        [--inputFile <inputFile>]
        Consider only files whose names contain this substring; every other
        file is ignored before the filters apply.

        [--fileFilter <token1,token2,...>]
        Keep only the files whose names pass these comma-separated tokens;
        a directory left with no file is not processed. A token is one of:

            nii                 substring: the file name contains 'nii'
            glob:*.nii.gz       a shell glob over the whole file name
            re:^sub-\\d+$        a regular expression, searched anywhere
            !tmp                negation of any of the above forms

        With --fileFilterLogic OR, the default, a file passing any token is
        kept; with AND, only a file passing every token. So "png,jpg" keeps
        PNG and JPEG files, and "glob:*.nii.gz,!tmp" with AND keeps the
        gzipped NIfTI files whose names do not contain "tmp".

        [--fileFilterLogic AND|OR]
        How --fileFilter's tokens combine: OR, the default, or AND.

        [--dirFilter <token1,token2,...>]
        Keep only the directories whose path passes these tokens, of the
        same forms as --fileFilter's. The path is the directory's path
        relative to the <inputDir>, with '/' between components, so the
        <inputDir>'s own name is never part of it, and

            --dirFilter 're:/anat$'
            --dirFilter 'glob:*/anat'

        anchor on a component. A file is processed only when its directory
        passes --dirFilter and its name passes --fileFilter.

        [--dirFilterLogic AND|OR]
        How --dirFilter's tokens combine: OR, the default, or AND.

        [--outputLeafDir <outputLeafDirFormat>]
        Rename the last component of each output directory with a pattern
        in which %s stands for the input directory's name, so that

            --outputLeafDir 'preview-%s'

        writes the results for /incoming/sub-01 to /outgoing/preview-sub-01.
        The pattern must contain exactly one %s.

        [--threads <numThreads>]
        Analyze up to <numThreads> directories at once, in a pool of
        threads; 0, the default, analyzes one directory at a time. The read
        stage before the analysis and the write stage after it stay
        sequential. A pool pays when each analysis waits on something
        outside Python, such as a program it runs, as pfdo_run's commands
        do; analysis in Python itself runs in parallel only on a
        free-threaded Python (python3.13t and later).

        On a free-threaded Python the same number of threads also scans the
        <inputDir>, dividing the tree at --threadsSplit. Under the ordinary
        interpreter the scan stays single-threaded and a warning says so.

        [--threadsSplit <level>]
        The directory level at which a threaded scan divides the tree: the
        directories at that level below the <inputDir> become the workers'
        jobs, while the levels above are walked by the main thread. Level 1
        (the default) gives one job per first-level subdirectory; when one
        of those holds most of the files, level 2 or 3 divides it further
        and balances the workers. Clamped to --maxDepth.

        [--json]
        Print the run's whole record as JSON on standard output when it
        ends: the directories found and their files, and each stage's result
        per directory.

        [--followLinks]
        Descend into symbolic links to directories, each target once;
        without it a link to a directory is not entered.

        [--overwrite]
        Let the run replace output files that already exist. It governs the
        files the tool writes itself, such as --test's; a program the tool
        runs decides for itself whether to overwrite.

        [--printElapsedTime]
        Print how long the run took, in seconds, when it ends.

        [--man]
        Print this manual and exit.

        [--synopsis]
        Print the manual's NAME, SYNOPSIS and brief example and exit.

        [--verbosity <level>]
        How much to say on the console: 0 errors only and no progress bars;
        1, the default, warnings and progress bars; 2 also what each stage
        is doing; 3 and above, debugging detail.

        [--version]
        Print the name and version and exit.
"""

EXAMPLES: str = """

    EXAMPLES

    * Run on a target tree and output some detail and stats

        pftree          --inputDir /var/www/html                                \\
                        --printElapsedTime                                      \\
                        --stats --verbosity 0 --json

    which will output only at script conclusion and will log a JSON formatted
    string.

    * Disk usage exactly as du reports it, into xdu:

        pftree          --du --inputDir /var/www/html | xdu

    * Keep only the gzipped NIfTI files that are not temporaries, in anat
      directories:

        pftree          --inputDir /data --relativeDir                          \\
                        --dirFilter 're:/anat$'                                 \\
                        --fileFilter 'glob:*.nii.gz,!tmp'                       \\
                        --fileFilterLogic AND --json

    * Run a test down a target tree:

        pftree          --inputDir /etc                                         \\
                        --outputDir /tmp/test                                   \\
                        --verbosity 1 --relativeDir                             \\
                        --outputLeafDir 'preview-%s'                            \\
                        --test 0

    which will "copy" the input tree to the output, and save a file-ls.txt
    in each directory where necessary. Note  for 'relative' directory
    specification and the ``--outputLeafDir`` spec.

"""

VERBOSITY_LOG_LEVELS: dict[int, int] = {0: logging.ERROR, 1: logging.WARNING, 2: logging.INFO}


def synopsis(shortOnly: bool = False) -> str:
    """Assemble the manual.

    Args:
        shortOnly: return only NAME, SYNOPSIS and a brief example.

    Returns:
        The manual text, without the banner.
    """
    shortSynopsis: str = (
        """
    NAME

        pftree - Create a dictionary representation of a filesystem tree.

    SYNOPSIS

        pftree \\"""
        + CLI_IO_CORE
        + CLI_SELF
        + CLI_CORE
        + """

    BRIEF EXAMPLE

        pftree                                                                  \\
                --inputDir /var/www/html                                        \\
                --outputDir /tmp                                                \\
                --relativeDir                                                   \\
                --printElapsedTime                                              \\
                --stats

    """
    )
    description: str = (
        """
    DESCRIPTION

        ``pftree`` in and of itself does not really do any work. It is a
        class that provides the internals for representing file system
        hierarchies in dictionary form.

        As a convenience, however, the ``--stats`` or ``--statsReverse``
        flags do provide a useful analog for sorted directory usage down
        a file system tree.

        Given an ``<inputDir>``, ``pftree`` will perform a recursive walk down
        the directory tree. For each directory that contains files,
        ``pftree`` will create a dictionary key of the directory path,
        and will store a list of filenames for the key value.

        The core of the class is the tree_process() method. When called,
        this method will loop over the dictionary, and for each key (i.e.
        'path') will call, in order, an inputRead_callback, an
        inputAnalyze_callback and an outputSave_callback. Each callback is
        passed the DirNode at that key (its path, its files and where its
        output goes) and returns a StageResult. A subclass overrides these
        three methods to do real work.

    ARGS """
        + ARGS_SYNOPSIS_IO
        + ARGS_SYNOPSIS_CORE
        + ARGS_SYNOPSIS_SELF
        + EXAMPLES
    )
    if shortOnly:
        return shortSynopsis
    return shortSynopsis + description


def manual_build() -> Manual:
    """The pftree manual page.

    Returns:
        The page, with both synopses assembled.
    """
    return Manual(
        command="pftree",
        art=BANNER_ART,
        title=BANNER_TITLE,
        text=BANNER_TEXT,
        version=__version__,
        synopsisShort=synopsis(shortOnly=True),
        synopsisFull=synopsis(shortOnly=False),
    )


def parserIO_build() -> argparse.ArgumentParser:
    """The input and output directory arguments of the standalone command.

    Returns:
        A parent parser with ``--inputDir`` and ``--outputDir``.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--inputDir", "--inputdir", dest="inputDir", default="", help="directory to walk (required)"
    )
    parser.add_argument(
        "--outputDir",
        "--outputdir",
        dest="outputDir",
        default=".",
        help="where per-directory output goes",
    )
    return parser


def parserCore_build(command: str = "pftree", version: str = __version__) -> argparse.ArgumentParser:
    """The engine options shared with packages built on pftree.

    Args:
        command: the name ``--version`` prints, for a package built on pftree.
        version: the version ``--version`` prints.

    Returns:
        A parent parser with the walk, filter, output and threading options.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--maxDepth", dest="maxDepth", type=int, default=-1, help="levels to descend; -1 for all"
    )
    parser.add_argument(
        "--inputFile", dest="inputFile", default="", help="list only files whose name contains this"
    )
    parser.add_argument(
        "--fileFilter", dest="fileFilter", default="", help="comma-separated file tokens (see --man)"
    )
    parser.add_argument(
        "--fileFilterLogic",
        dest="fileFilterLogic",
        default="OR",
        choices=["AND", "OR", "and", "or"],
        help="combine file tokens with AND or OR (default OR)",
    )
    parser.add_argument(
        "--dirFilter", dest="dirFilter", default="", help="comma-separated directory tokens (see --man)"
    )
    parser.add_argument(
        "--dirFilterLogic",
        dest="dirFilterLogic",
        default="OR",
        choices=["AND", "OR", "and", "or"],
        help="combine directory tokens with AND or OR (default OR)",
    )
    parser.add_argument(
        "--outputLeafDir",
        dest="outputLeafDir",
        default="",
        help="template for each output leaf name; %%s is the input leaf, e.g. 'anon-%%s'",
    )
    parser.add_argument(
        "--relativeDir", dest="relativeDir", action="store_true", help="key the tree by relative paths"
    )
    parser.add_argument(
        "--followLinks", dest="followLinks", action="store_true", help="follow symbolic links"
    )
    parser.add_argument(
        "--overwrite", dest="overwrite", action="store_true", help="allow overwriting existing output"
    )
    parser.add_argument(
        "--threads",
        dest="threads",
        type=int,
        default=0,
        help="workers for the scan (free-threaded Python only) and the analyze stage; 0 = sequential",
    )
    parser.add_argument(
        "--threadsSplit",
        dest="threadsSplit",
        type=int,
        default=1,
        help="directory level the scan's threads split the tree at (default 1)",
    )
    parser.add_argument("--verbosity", dest="verbosity", type=int, default=1, help="0 silences the console")
    parser.add_argument("--json", dest="json", action="store_true", help="print the full result as JSON")
    parser.add_argument(
        "--printElapsedTime", dest="printElapsedTime", action="store_true", help="print the run time"
    )
    parser.add_argument("--man", dest="man", action=ManualAction, help="show the full manual and exit")
    parser.add_argument(
        "--synopsis", dest="synopsis", action=ManualAction, help="show the brief manual and exit"
    )
    parser.add_argument("--version", action="version", version=f"{command} {version}")
    return parser


def parserSelf_build() -> argparse.ArgumentParser:
    """The options specific to the pftree command itself.

    Returns:
        A parent parser with the stats and test options.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--stats", dest="stats", action="store_true", help="disk usage per directory, largest last"
    )
    parser.add_argument(
        "--statsReverse", dest="statsReverse", action="store_true", help="disk usage, largest first"
    )
    parser.add_argument(
        "--du", dest="du", action="store_true", help="print what 'du -k' prints; pipe into xdu"
    )
    parser.add_argument("--duf", dest="duf", action="store_true", help="print what 'du -h' prints")
    parser.add_argument(
        "--apparentSize",
        dest="apparentSize",
        action="store_true",
        help="du counts st_size, like du --apparent-size",
    )
    parser.add_argument("--jsonStats", dest="jsonStats", action="store_true", help="print the stats as JSON")
    parser.add_argument(
        "--test",
        dest="test",
        default="",
        help="run the test callbacks; '<seconds>[:<type>]' sleeps per directory, type 1 writes counts",
    )
    return parser


def parser_build(standalone: bool = True) -> argparse.ArgumentParser:
    """Assemble the full parser.

    Args:
        standalone: include the ``--inputDir`` / ``--outputDir`` arguments. A ChRIS DS
            plugin passes False and adds its own positional directories.

    Returns:
        The parser; ``--help`` is the quick reference and points at ``--man``, which
        with ``--synopsis`` prints pftree's manual and exits as it is read.
    """
    parents: list[argparse.ArgumentParser] = [parserCore_build(), parserSelf_build()]
    if standalone:
        parents.insert(0, parserIO_build())
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="pftree",
        description="Create a dictionary representation of a filesystem tree. Full manual: pftree --man",
        epilog="Filter tokens: substring, 'glob:<pattern>', 're:<regex>', each negatable with a leading '!'.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=parents,
    )
    manual_attach(parser, manual_build())
    return parser


def options_fromArgs(args: argparse.Namespace) -> TreeOptions:
    """Turn parsed arguments into engine options.

    Args:
        args: the parser's Namespace.

    Returns:
        TreeOptions with every engine field applied; extra arguments are ignored.
    """
    return TreeOptions.fromDict(vars(args))


def logging_configure(verbosity: int) -> None:
    """Map verbosity to the pftree logger's level.

    Args:
        verbosity: 0 errors only, 1 warnings, 2 info, 3 and up debug.
    """
    level: int = VERBOSITY_LOG_LEVELS.get(verbosity, logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def du_render(report: DuReport, human: bool) -> str:
    """Format the ``du`` report exactly as ``du`` would.

    Args:
        report: the lines, post-order, root last.
        human: ``du -h`` sizes instead of ``du -k`` kilobytes.

    Returns:
        ``<size>\\t<path>`` per line.
    """
    lines: list[str] = []
    for line in report.lines:
        size: str = du_humanize(line.byteCount) if human else str(line.kilobytes)
        lines.append(f"{size}\t{line.path}")
    return "\n".join(lines)


def stats_render(stats: StatsResult) -> str:
    """Format the per-directory report.

    Args:
        stats: the report to render.

    Returns:
        One table line per directory, then the totals block.
    """
    lines: list[str] = [
        f"files: {stat.files:5d}│ raw_size: {stat.sizeBytes:12d}│ "
        f"human_size: {stat.sizeHuman:>8}│ dir: {stat.path}"
        for stat in stats.perDir
    ]
    lines.extend(
        [
            "",
            f"Total size (raw):        {stats.totalSize}",
            f"Total size (friendly):   {stats.totalSize:,}",
            f"Total size (human):      {stats.totalSizeHuman}",
            f"Total files:             {stats.files}",
            f"Total dirs:              {stats.dirs}",
        ]
    )
    return "\n".join(lines)


def result_report(args: argparse.Namespace, result: RunResult) -> None:
    """Print whatever the arguments asked for.

    Args:
        args: the parsed arguments.
        result: the engine's result.
    """
    if args.json:
        print(json.dumps(result.toDict(), indent=4, sort_keys=True))
        return
    if result.du is not None:
        print(du_render(result.du, human=args.duf))
    if result.stats is not None:
        if args.jsonStats:
            print(json.dumps(result.toDict()["stats"], indent=4, sort_keys=True))
        else:
            print(stats_render(result.stats))
    if args.printElapsedTime:
        print(f"Elapsed time = {result.runTime:.6f} seconds")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command.

    Args:
        argv: arguments without the program name; ``None`` reads ``sys.argv``.

    Returns:
        The exit code: 0 on success, 1 when the run failed, 2 on bad arguments.
    """
    parser: argparse.ArgumentParser = parser_build()
    args: argparse.Namespace = parser.parse_args(argv)
    if not args.inputDir:
        parser.error("--inputDir is required")
    try:
        options: TreeOptions = options_fromArgs(args)
    except ValueError as err:
        parser.error(str(err))
    logging_configure(options.verbosity)
    result: RunResult = Pftree(options).run()
    result_report(args, result)
    if result.error:
        print(result.error, file=sys.stderr)
    return 0 if result.status else 1


if __name__ == "__main__":
    sys.exit(main())
