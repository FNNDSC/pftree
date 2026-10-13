pftree
======

.. image:: https://github.com/FNNDSC/pftree/actions/workflows/ci.yml/badge.svg
    :target: https://github.com/FNNDSC/pftree/actions/workflows/ci.yml

.. image:: https://img.shields.io/pypi/v/pftree.svg
    :target: https://pypi.org/project/pftree/

.. image:: https://img.shields.io/badge/python-3.11%2B-blue.svg
    :target: https://pypi.org/project/pftree/

A dictionary representation of a filesystem tree, with per-directory read / analyze / write processing.

.. contents:: Table of Contents

Overview
--------

``pftree`` walks an input directory and records, for every directory that holds files, the names of those files. That mapping is the ``tree``. On top of it the engine runs a three-stage sequence over every directory, read, analyze and write, mirroring the input layout under an output directory. Packages such as ``pfdo`` and ``pfdicom`` subclass the engine and override the three callbacks; on its own, ``pftree`` prints trees as JSON and reports disk usage per directory.

Installation
------------

.. code:: bash

    pip install pftree

Python 3.11 or newer. The only dependency is ``tqdm``.

Command line
------------

.. code:: bash

    pftree --inputDir /data --stats
    pftree --inputDir /data --du
    pftree --inputDir /data --fileFilter 'glob:*.nii.gz,!tmp' --fileFilterLogic AND --json
    pftree --inputDir /data --dirFilter 're:/anat$' --json
    pftree --inputDir /data --outputDir /tmp/out --outputLeafDir 'preview-%s' --test 0

``pftree --man`` prints the full manual, ``pftree --synopsis`` its first page, ``pftree --help`` argparse's quick reference. ``--inputDir`` (required) and ``--outputDir`` name the two trees. ``--maxDepth`` bounds the walk (``-1`` for all levels). ``--relativeDir`` keys the tree by paths relative to the input directory. ``--followLinks`` follows symbolic links. ``--threads N`` runs the analyze stage in a pool of N workers. ``--json`` prints the whole result. ``--du`` prints exactly what ``du -k`` prints (cumulative kilobytes per directory, every directory, children before parents, the input directory last, hard links counted once), so ``pftree --du --inputDir /data | xdu`` works; ``--duf`` is the ``du -h`` form and ``--apparentSize`` the ``du --apparent-size`` count. ``--stats``, ``--statsReverse`` and ``--jsonStats`` print pftree's own per-directory report: file count and apparent size of the files each directory holds, after any filters. ``--test <seconds>[:<type>]`` runs the built-in callbacks, sleeping per directory and writing a file listing (type 0) or a file count (type 1) into each output directory.

Filters
-------

``--fileFilter`` and ``--dirFilter`` each take a comma-separated list of tokens. A token is one of:

===================== ============================================================
``nii``               substring: the text contains ``nii``
``glob:*.nii.gz``     shell glob over the whole text
``re:^sub-\d+$``      regular expression, searched anywhere in the text
``!tmp``              negation of any of the above forms
===================== ============================================================

``--fileFilterLogic`` and ``--dirFilterLogic`` combine a filter's tokens with ``OR`` (any token, the default) or ``AND`` (every token).

A file filter tests each file's name. A directory filter tests each directory's path relative to ``--inputDir`` with ``/`` separators, so the input root's own name never matches, and ``glob:*/anat`` or ``re:/anat$`` anchor on a component. The two filters are always ANDed: a file is kept only when its directory passes too, and a directory that keeps no files leaves the tree.

Library
-------

.. code:: python

    from pathlib import Path
    from pftree import DirNode, Pftree, StageResult, TreeOptions

    class Counter(Pftree):
        def inputAnalyze_callback(self, node: DirNode, read: StageResult) -> StageResult:
            return StageResult(status=True, files=read.files, payload={"count": len(read.files)})

        def outputSave_callback(self, node: DirNode, analyze: StageResult) -> StageResult:
            node.outputPath.mkdir(parents=True, exist_ok=True)
            (node.outputPath / "count.txt").write_text(f"{analyze.payload['count']}\n")
            return StageResult(status=True, files=["count.txt"])

    engine = Counter(TreeOptions(inputDir=Path("/data"), outputDir=Path("/out"), threads=4))
    engine.tree_construct(engine.tree_probe())
    engine.tree_filter()
    result = engine.tree_process(
        inputRead_callback=engine.inputRead_callback,
        inputAnalyze_callback=engine.inputAnalyze_callback,
        outputWrite_callback=engine.outputSave_callback,
    )

Every option and result type lives in ``pftree.models``. ``TreeOptions`` holds every option; ``TreeOptions.fromDict(vars(args))`` builds it from an argparse ``Namespace`` and ignores keys it does not know, so a subclass with a larger argument set passes its namespace straight through. ``pftree.__main__`` exposes ``parserCore_build()`` and ``parserSelf_build()`` as parent parsers, and the ``CLI_*`` and ``ARGS_SYNOPSIS_*`` manual texts, for packages that add their own input and output arguments and compose their own ``--man`` page.

Each callback receives a ``DirNode`` (``path``, ``relPath``, sorted ``files``, ``outputPath``) and returns a ``StageResult`` (``status``, ``files``, ``payload``). Reads and writes run sequentially; analyses run in the thread pool when ``threads`` is positive. A directory whose analyze result has ``status`` False, or whose analyze callback raises, is dropped from the tree before the write stage; the exception is logged with its traceback. ``tree_process`` returns a ``ProcessResult`` with the per-directory results and file counts.

Changes in 4.0
--------------

- ``pfmisc`` and ``pudb`` are no longer dependencies; logging goes through the standard ``logging`` module under the ``pftree`` logger.
- ``--outputLeafDir`` is applied with ordinary ``%`` formatting; the template must contain ``%s``.
- The callback methods are ``inputRead_callback``, ``inputAnalyze_callback`` and ``outputSave_callback``, and they take and return typed objects rather than tuples and dicts.
- ``--dirFilter`` matches the path relative to the input directory, not the absolute path.
- Filter tokens accept ``glob:``, ``re:`` and ``!`` prefixes.
- Files directly under the input directory are listed under the root key; earlier versions listed the root's subdirectory names there instead.
- ``--relativeDir`` no longer changes the process's working directory.
- The ``--3D`` and ``--syslog`` options are gone.
- ``main(argv)`` honours ``argv``; ``--version``, ``--man`` and ``--synopsis`` exit 0.
- Packaging is ``pyproject.toml`` only, published from CI on a ``v*`` tag.
