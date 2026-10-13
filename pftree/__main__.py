"""The ``pftree`` command line.

Walk a directory, print its tree as JSON, or report disk usage per directory in the
style of ``du``. The parser is built from three parts (I/O, core, self) so that a
package built on pftree, such as pfdo, can reuse the core and self parts and supply
its own I/O arguments.

Key components:
    - parserIO_build / parserCore_build / parserSelf_build: the argument groups
    - parser_build: the assembled parser, standalone or ChRIS-DS style
    - options_fromArgs: Namespace to TreeOptions
    - stats_render: the text report
    - main: the entry point, honouring an explicit ``argv``

Typical usage:
    pftree --inputDir /data --stats
    pftree --inputDir /data --fileFilter 'glob:*.dcm' --json

Dependencies:
    - pftree.tree, pftree.filters
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from pftree import __version__
from pftree.tree import Pftree, RunResult, StatsResult, TreeOptions

DESCRIPTION: str = """pftree: a dictionary representation of a filesystem tree.

Walk an input directory and record, for every directory that holds files, the names
of those files. Filter what is kept with --fileFilter and --dirFilter, print the tree
with --json, or report disk usage per directory with --stats or --du.

The engine behind this command runs a read / analyze / write sequence over every
directory, mirroring the input layout under --outputDir; packages such as pfdo build
on it. --test exercises that sequence with built-in callbacks."""

FILTER_HELP: str = """comma-separated tokens; each is a substring, 'glob:<pattern>',
're:<regex>', or any of those with a leading '!' to negate"""

EPILOG: str = """examples:
  pftree --inputDir /data --stats
  pftree --inputDir /data --du
  pftree --inputDir /data --fileFilter 'glob:*.nii.gz,!tmp' --fileFilterLogic AND --json
  pftree --inputDir /data --dirFilter 're:/anat$' --json
  pftree --inputDir /data --outputDir /tmp/out --outputLeafDir 'preview-%s' --test 0

filters:
  --fileFilter tests each file's name. --dirFilter tests each directory's path relative
  to --inputDir with '/' separators, so the input root's own name never matches and
  'glob:*/anat' or 're:/anat$' anchor on a component. Files and directories are always
  ANDed: a file is kept only when its directory passes too."""

VERBOSITY_LOG_LEVELS: dict[int, int] = {0: logging.ERROR, 1: logging.WARNING, 2: logging.INFO}


def parserIO_build() -> argparse.ArgumentParser:
    """The input and output directory arguments of the standalone command.

    Returns:
        A parent parser with ``--inputDir`` and ``--outputDir``.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--inputDir", "--inputdir", dest="inputDir", required=True, help="directory to walk")
    parser.add_argument(
        "--outputDir", "--outputdir", dest="outputDir", default=".", help="where per-directory output goes"
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
    parser.add_argument("--fileFilter", dest="fileFilter", default="", help=FILTER_HELP)
    parser.add_argument(
        "--fileFilterLogic",
        dest="fileFilterLogic",
        default="OR",
        choices=["AND", "OR", "and", "or"],
        help="combine file tokens with AND or OR (default OR)",
    )
    parser.add_argument(
        "--dirFilter", dest="dirFilter", default="", help=FILTER_HELP + ", on the relative path"
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
    parser.add_argument("--man", dest="man", action="store_true", help="show the full help and exit")
    parser.add_argument(
        "--synopsis", dest="synopsis", action="store_true", help="show the usage line and exit"
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
        The parser.
    """
    parents: list[argparse.ArgumentParser] = [parserCore_build(), parserSelf_build()]
    if standalone:
        parents.insert(0, parserIO_build())
    return argparse.ArgumentParser(
        prog="pftree",
        description=DESCRIPTION,
        epilog=EPILOG,
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command.

    Args:
        argv: arguments without the program name; ``None`` reads ``sys.argv``.

    Returns:
        The exit code: 0 on success, 1 when the run failed, 2 on bad arguments.
    """
    parser: argparse.ArgumentParser = parser_build()
    args: argparse.Namespace = parser.parse_args(argv)
    if args.man:
        parser.print_help()
        return 0
    if args.synopsis:
        parser.print_usage()
        return 0
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
