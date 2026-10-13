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
import re
import shutil
import sys
from collections.abc import Sequence

from pftree import __version__
from pftree.models import RunResult, StatsResult, TreeOptions
from pftree.tree import Pftree

NO_COLOUR: str = "\033[0m"
BG_PAGE: str = "\033[40m"
FG_BODY: str = "\033[97m"
FG_BANNER: str = "\033[36m"
FG_VERSION: str = "\033[1;33m"
FG_HEADING: str = "\033[1;33m"
FG_COMMAND: str = "\033[1;32m"
FG_FLAG: str = "\033[36m"
FG_PLACEHOLDER: str = "\033[35m"
FG_STRING: str = "\033[33m"
FG_CONTINUATION: str = "\033[90m"
FALLBACK_WIDTH: int = 100

MANUAL_HEADING: re.Pattern[str] = re.compile(
    r"^\s{4}(NAME|SYNOPSIS|BRIEF EXAMPLE|DESCRIPTION|ARGS|EXAMPLES)\s*$"
)
MANUAL_CLI_LINE: re.Pattern[str] = re.compile(r"^\s{8,}(pftree\b|\[--|--|<inputDir>|<outputDir>|/|\.\.\.)")
CLI_TOKEN: re.Pattern[str] = re.compile(
    r"(?P<command>\bpftree\b)"
    r"|(?P<flag>--[A-Za-z0-9]+)"
    r"|(?P<string>'[^']*')"
    r"|(?P<placeholder><[^<>]+>)"
    r"|(?P<continuation>\\$)"
)
INLINE_CODE: re.Pattern[str] = re.compile(r"``([^`]+)``")
CLI_TOKEN_COLOURS: dict[str, str] = {
    "command": FG_COMMAND,
    "flag": FG_FLAG,
    "string": FG_STRING,
    "placeholder": FG_PLACEHOLDER,
    "continuation": FG_CONTINUATION,
}

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
        [--json]
"""

CLI_SELF: str = """
        [--stats | --statsReverse | --du | --duf]                               \\
        [--jsonStats]                                                           \\
        [--test <analysisDelayLength[:<type>]>]                                 \\"""

ARGS_SYNOPSIS_SELF: str = """
        [--stats | --statsReverse | --du | --duf]
        If specified, return some stats to caller. The amount of information
        returned depends on the --verbosity.

        For --stats (and --statsReverse):

            * --verbosity 0: return only a final summary of group statistics
            * --verbosity 1: in addition, return a sorted (by size) list of
                             subdirectories in the search tree
            * --verbosity >1: same as above, but provide probing status updates.

        For --du | --duf

            similar to '--stats' but return directory lists in a fashion similar
            to the GNU 'du' tool:

            * --du : the sorted list and a summary
            * --duf: the sorted list only

        [--jsonStats]
        If specified, do a JSON dump of the stats.

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
        Input directory to examine. The downstream nested structure of this
        directory is examined and recreated in the <outputDir>.

        [--outputDir <outputDir>]
        The directory to contain a tree structure identical to the input
        tree structure, and which contains all output files from the
        per-input-dir processing.
"""

ARGS_SYNOPSIS_DS: str = """

        <inputDir>
        A _required_ positional argument for ChRIS DS plugins denoting the
        input directory to examine. The downstream nested structure of this
        directory is examined and recreated in the <outputDir>.

        <outputDir>
        A _required_ positional argument for ChRIS DS plugins denoting the
        output directory to store data. The structure of this tree mimics
        that of the <inputDir>, with correspondant directory nodes containing
        the results of corresponding <inputDir> processing.
"""

ARGS_SYNOPSIS_CORE: str = """

        [--maxDepth <dirDepth>]
        The maximum depth to descend relative to the <inputDir>. Note, that
        this counts from zero! Default of '-1' implies traverse the entire
        directory tree.

        [--relativeDir]
        A flag argument. If passed (i.e. True), then the dictionary key values
        are taken to be relative to the <inputDir>, i.e. the key values
        will not contain the <inputDir>; otherwise the key values will
        contain the <inputDir>.

        [--inputFile <inputFile>]
        An optional substring. If specified, only files whose names contain
        this substring are listed in the tree; every other file is ignored
        before any filter is applied.

        [--fileFilter <token1,token2,...>]
        An optional comma-delimited list of tokens to filter the files of
        interest from the <inputDir> tree. Each token is applied over the
        files in a directory, and only files that satisfy the tokens (under
        the --fileFilterLogic) are preserved. A token is one of:

            nii                 substring: the file name contains 'nii'
            glob:*.nii.gz       a shell glob over the whole file name
            re:^sub-\\d+$        a regular expression, searched anywhere
            !tmp                negation of any of the above forms

        Thus, a --fileFilter of "png,jpg,body" keeps files containing "png"
        OR "jpg" OR "body"; a --fileFilter of "glob:*.nii.gz,!tmp" with
        --fileFilterLogic AND keeps the gzipped NIfTI files that do not
        contain "tmp".

        [--fileFilterLogic AND|OR]
        The logical operator to apply across the fileFilter tokens. Default
        is OR.

        [--dirFilter <token1,token2,...>]
        An additional filter that will further limit any files to process to
        only those files that exist in directories whose path satisfies the
        tokens. The same token forms as --fileFilter apply. The path tested
        is the directory's path relative to the <inputDir>, with '/' as the
        separator, so the name of the <inputDir> itself never matches, and

            --dirFilter 're:/anat$'
            --dirFilter 'glob:*/anat'

        anchor on a path component. A directory filter of "100307,100556"
        and a file filter of "png,jpg" reduce the space to ONLY files that
        have an ancestor directory containing "100307" OR "100556" AND that
        contain either "png" OR "jpg" in their file names: the relationship
        between the file and directory filters is always AND.

        [--dirFilterLogic AND|OR]
        The logical operator to apply across the dirFilter tokens. Default
        is OR.

        [--outputLeafDir <outputLeafDirFormat>]
        If specified, will apply the <outputLeafDirFormat> to the output
        directories containing data. This is useful to blanket describe
        final output directories with some descriptive text, such as
        'anon' or 'preview'.

        This is a formatting spec, so

            --outputLeafDir 'preview-%s'

        where %s is the original leaf directory node, will prefix each
        final directory containing output with the text 'preview-' which
        can be useful in describing some features of the output set. The
        spec must contain exactly one '%s'.

        [--threads <numThreads>]
        If specified, break the innermost analysis loop into <numThreads>
        threads. Please note the following caveats:

            * Only thread if you have a high CPU analysis loop. Note that
              the input file read and output file write loops are not
              threaded -- only the analysis loop is threaded. Thus, if the
              bulk of execution time is in file IO, threading will not
              really help.

            * Threading will change the nature of the innermost looping
              across the problem domain, with the result that *all* of the
              problem data will be read into memory! That means potentially
              all the target input file data across the entire input directory
              tree.

        [--json]
        If specified, do a JSON dump of the entire return payload.

        [--followLinks]
        If specified, follow symbolic links.

        [--overwrite]
        If specified, allow for overwriting of existing files.

        [--printElapsedTime]
        If specified, print the run time at the end.

        [--man]
        Show full help.

        [--synopsis]
        Show brief help.

        [--verbosity <level>]
        Set the app verbosity level. This ranges from 0...<N> where internal
        log messages with a level=<M> will only display if M <= N. In this
        manner increasing the level here can be used to show more and more
        debugging info, assuming that debug messages in the code have been
        tagged with a level. A verbosity of 0 also silences progress bars.

        [--version]
        Print the version number and exit.
"""

EXAMPLES: str = """

    EXAMPLES

    * Run on a target tree and output some detail and stats

        pftree          --inputDir /var/www/html                                \\
                        --printElapsedTime                                      \\
                        --stats --verbosity 0 --json

    which will output only at script conclusion and will log a JSON formatted
    string. Similarly

        pftree          --du --inputDir /var/www/html

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


def page_width() -> int:
    """The terminal width the manual is padded to.

    Returns:
        The current terminal's column count, or a fallback when there is none.
    """
    return shutil.get_terminal_size((FALLBACK_WIDTH, 24)).columns


def line_onPage(coloured: str, visibleLength: int, width: int, foreground: str) -> str:
    """Set one line on the manual's black page.

    Args:
        coloured: the line with any inline colour codes already applied.
        visibleLength: the line's length without colour codes.
        width: the page width to pad to.
        foreground: the colour the line starts in.

    Returns:
        The line with the page background, padded to the width, reset at the end.
    """
    padding: str = " " * max(width - visibleLength, 0)
    return BG_PAGE + foreground + coloured + padding + NO_COLOUR


def banner_render(colour: bool, width: int = FALLBACK_WIDTH) -> str:
    """The title block of the manual.

    Args:
        colour: set the banner on the black page, cyan, with the version in yellow.
        width: the page width when coloured.

    Returns:
        The banner text.
    """
    if not colour:
        return BANNER_ART + BANNER_TEXT.format(version=__version__)
    lines: list[str] = []
    for line in (BANNER_ART + BANNER_TEXT.format(version="{version}")).splitlines():
        visible: str = line.replace("{version}", __version__)
        coloured: str = line.replace("{version}", FG_VERSION + __version__ + FG_BANNER)
        lines.append(line_onPage(coloured, len(visible), width, FG_BANNER))
    return "\n".join(lines)


def cliLine_highlight(line: str) -> str:
    """Colour a command-line example: command, flags, quoted strings, placeholders.

    Args:
        line: one line of a SYNOPSIS or EXAMPLES block.

    Returns:
        The line with each recognised token coloured and the body colour restored
        after it.
    """

    def token_colour(match: re.Match[str]) -> str:
        kind: str = str(match.lastgroup)
        return CLI_TOKEN_COLOURS[kind] + match.group(0) + FG_BODY

    return CLI_TOKEN.sub(token_colour, line)


def inlineCode_render(line: str, colour: bool) -> str:
    """Replace ````code```` spans in prose with the bare text, coloured as a flag.

    Args:
        line: one prose line of the manual.
        colour: colour the span; otherwise only drop the backticks.

    Returns:
        The line with every span rewritten.
    """
    if colour:
        return INLINE_CODE.sub(lambda match: FG_FLAG + match.group(1) + FG_BODY, line)
    return INLINE_CODE.sub(lambda match: match.group(1), line)


def manual_plain(text: str) -> str:
    """The manual without colour and without the inline-code backticks.

    Args:
        text: the manual as ``synopsis`` returns it.

    Returns:
        The text for a pipe or a file.
    """
    return "\n".join(inlineCode_render(line, colour=False) for line in text.splitlines())


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


def parserCore_build() -> argparse.ArgumentParser:
    """The engine options shared with packages built on pftree.

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
        "--threads", dest="threads", type=int, default=0, help="analyze-stage workers; 0 = sequential"
    )
    parser.add_argument("--verbosity", dest="verbosity", type=int, default=1, help="0 silences the console")
    parser.add_argument("--json", dest="json", action="store_true", help="print the full result as JSON")
    parser.add_argument(
        "--printElapsedTime", dest="printElapsedTime", action="store_true", help="print the run time"
    )
    parser.add_argument("--man", dest="man", action="store_true", help="show the full manual and exit")
    parser.add_argument(
        "--synopsis", dest="synopsis", action="store_true", help="show the brief manual and exit"
    )
    parser.add_argument("--version", action="version", version=f"pftree {__version__}")
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
    parser.add_argument("--du", dest="du", action="store_true", help="du-style lines plus a summary")
    parser.add_argument("--duf", dest="duf", action="store_true", help="du-style lines, no summary")
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
        The parser; ``--help`` is the quick reference and points at ``--man``.
    """
    parents: list[argparse.ArgumentParser] = [parserCore_build(), parserSelf_build()]
    if standalone:
        parents.insert(0, parserIO_build())
    return argparse.ArgumentParser(
        prog="pftree",
        description="Create a dictionary representation of a filesystem tree. Full manual: pftree --man",
        epilog="Filter tokens: substring, 'glob:<pattern>', 're:<regex>', each negatable with a leading '!'.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=parents,
    )


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


def stats_render(stats: StatsResult, duStyle: bool, summary: bool) -> str:
    """Format the disk-usage report.

    Args:
        stats: the report to render.
        duStyle: ``<size>  <path>`` lines like ``du -h``; else a wider table.
        summary: append the totals block.

    Returns:
        The report text, lines joined with newlines.
    """
    lines: list[str] = []
    for stat in stats.perDir:
        if duStyle:
            lines.append(f"{stat.sizeHuman:<10}{stat.path}")
        else:
            lines.append(
                f"files: {stat.files:5d}│ raw_size: {stat.sizeBytes:12d}│ "
                f"human_size: {stat.sizeHuman:>8}│ dir: {stat.path}"
            )
    if summary:
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
    if result.stats is not None:
        if args.jsonStats:
            print(json.dumps(result.toDict()["stats"], indent=4, sort_keys=True))
        else:
            duStyle: bool = args.du or args.duf
            print(stats_render(result.stats, duStyle=duStyle, summary=not args.duf))
    if args.printElapsedTime:
        print(f"Elapsed time = {result.runTime:.6f} seconds")


def manual_colourize(text: str, width: int = FALLBACK_WIDTH) -> str:
    """Set the manual on a black page: white body, yellow headings, highlighted CLI.

    Every line gets the page background and is padded to ``width`` so the page is a
    solid block whatever the terminal's own background. Section headings are yellow;
    lines that show command-line syntax (the SYNOPSIS, the option headers in ARGS and
    the EXAMPLES) get ``cliLine_highlight``; everything else is white.

    Args:
        text: the manual as ``synopsis`` returns it.
        width: the page width.

    Returns:
        The coloured manual.
    """
    page: list[str] = []
    for line in text.splitlines():
        if MANUAL_HEADING.match(line):
            page.append(line_onPage(line, len(line), width, FG_HEADING))
        elif MANUAL_CLI_LINE.match(line):
            page.append(line_onPage(cliLine_highlight(line), len(line), width, FG_BODY))
        else:
            visible: str = inlineCode_render(line, colour=False)
            page.append(line_onPage(inlineCode_render(line, colour=True), len(visible), width, FG_BODY))
    return "\n".join(page)


def manual_print(shortOnly: bool) -> None:
    """Print the banner and the manual, coloured when stdout is a terminal.

    Args:
        shortOnly: print the brief manual rather than the full one.
    """
    colour: bool = sys.stdout.isatty()
    manual: str = synopsis(shortOnly)
    if not colour:
        print(banner_render(colour))
        print(manual_plain(manual))
        return
    width: int = page_width()
    print(banner_render(colour, width))
    print(manual_colourize(manual, width))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command.

    Args:
        argv: arguments without the program name; ``None`` reads ``sys.argv``.

    Returns:
        The exit code: 0 on success, 1 when the run failed, 2 on bad arguments.
    """
    parser: argparse.ArgumentParser = parser_build()
    args: argparse.Namespace = parser.parse_args(argv)
    if args.man or args.synopsis:
        manual_print(shortOnly=not args.man)
        return 0
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
