"""pftree: a dictionary representation of a filesystem tree, with per-directory processing.

pftree walks an input directory, records which files live in which directory, and runs
a read / analyze / write sequence over every directory it found, mirroring the input
layout into an output directory. Subclasses override the three callbacks to do real work;
the module itself ships test callbacks and a ``--stats`` disk-usage report.

Key components:
    - Pftree: the engine (probe, construct, filter, process, stats)
    - TreeOptions: every knob the engine reads, buildable from a dict or an argparse Namespace
    - DirNode, StageResult, ProcessResult, StatsResult, RunResult: typed results
    - models: every option and result type
    - filters: the ``--fileFilter`` / ``--dirFilter`` token grammar
    - __main__: the ``pftree`` command line, its manual, and the parser factories reused by pfdo

Typical usage:
    from pftree import Pftree, TreeOptions
    tree = Pftree(TreeOptions(inputDir=Path("/data"), fileFilter="glob:*.dcm"))
    result = tree.run()
    for path, files in result.tree.items():
        ...

Dependencies:
    - tqdm, for progress bars when verbosity allows console output
"""

from __future__ import annotations

__version__: str = "4.0.0"

from pftree.filters import FilterLogic, FilterToken, dirPath_matches, fileNames_filter, tokens_parse
from pftree.models import (
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
from pftree.tree import Pftree

__all__ = [
    "DirNode",
    "DirStat",
    "FilterLogic",
    "FilterToken",
    "KeyedResult",
    "Pftree",
    "ProcessResult",
    "RunResult",
    "StageResult",
    "StatsResult",
    "TreeOptions",
    "WalkEntry",
    "__version__",
    "dirPath_matches",
    "fileNames_filter",
    "tokens_parse",
]
