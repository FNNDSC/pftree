pftree
======

.. image:: https://badge.fury.io/py/pftree.svg
    :target: https://badge.fury.io/py/pftree

.. image:: https://travis-ci.org/FNNDSC/pftree.svg?branch=master
    :target: https://travis-ci.org/FNNDSC/pftree

.. image:: https://img.shields.io/badge/python-3.5%2B-blue.svg
    :target: https://badge.fury.io/py/pftree

.. contents:: Table of Contents


Quick Overview
--------------

-  Create a dictionary representation of a filesystem hierarchy.
-  Optionally report some stats on the hierarchy (esp size of each directory).

Overview
--------

Given an ``<inputDir>``, ``pftree`` will perform a recursive walk down the directory tree. For each directory that contains files, ``pftree`` will create a dictionary key of the directory path, and will store a list of filenames for the key value.

``pftree`` in and of itself does not really do any work. It is a class/module that abstracts the internals for representing file system hierarchies in dictionary form to be used by other modules. As a convenience, however, the ``--stats`` or ``--statsReverse`` do provide a useful analog for sorted directory usage down a file system tree.

Several simple file and directory name filters can be applied which can facilitate the targetting of very specific elements in a file system tree.

The core the of the class is a ``tree_analysisApply()`` method, that accepts various kwargs. When called, this method will loop over the dictionary, and for each key (i.e. 'path') will execute a callback method. This callback is passed the dictionary value at that key (i.e. usually just the list of files) as well as all the kwargs passed to ``tree_analysisApply()``.

Installation
------------

Dependencies
~~~~~~~~~~~~

The following dependencies are installed on your host system/python3 virtual env (they will also be automatically installed if pulled from pypi):

-  ``pfmisc`` (various misc modules and classes for the pf* family of objects)
-  ``tqdm`` (console prettiness for progress bars)

Using ``PyPI``
~~~~~~~~~~~~~~

The best method of installing this script and all of its dependencies is
by fetching it from PyPI

.. code:: bash

        pip3 install pftree


Command line arguments
----------------------

.. code:: html

        --inputDir <inputDir>
        Input directory to examine. The downstream nested structure of this
        directory is examined and recreated in the <outputDir>.

        [--outputDir <outputDir>]
        The directory to contain a tree structure identical to the input
        tree structure, and which contains all output files from the
        per-input-dir processing.

        [--maxdepth <dirDepth>]
        The maximum depth to descend relative to the <inputDir>. Note, that
        this counts from zero! Default of '-1' implies transverse the entire
        directory tree.

        [--relativeDir]
        A flag argument. If passed (i.e. True), then the dictionary key values
        are taken to be relative to the <inputDir>, i.e. the key values
        will not contain the <inputDir>; otherwise the key values will
        contain the <inputDir>.

        [--inputFile <inputFile>]
        An optional <inputFile> specified relative to the <inputDir>. If
        specified, then do not perform a directory walk, but target this
        specific file.

        [--fileFilter <someFilter1,someFilter2,...>]
        An optional comma-delimated string to filter out files of interest
        from the <inputDir> tree. Each token in the expression is applied in
        turn over the space of files in a directory location according to a
        logical operation, and only files that contain this token string in
        their filename are preserved.

        [--filteFilterLogic AND|OR]
        The logical operator to apply across the fileFilter operation. Default
        is OR.

        [--dirFilter <someFilter1,someFilter2,...>]
        An additional filter that will further limit any files to process to
        only those files that exist in leaf directory nodes that have some
        substring of each of the comma separated <someFilter> in their
        directory name.

        [--dirFilterLogic AND|OR]
        The logical operator to apply across the dirFilter operation. Default
        is OR.

        [--outputLeafDir <outputLeafDirFormat>]
        If specified, will apply the <outputLeafDirFormat> to the output
        directories containing data. This is useful to blanket describe
        final output directories with some descriptive text, such as
        'anon' or 'preview'.

        This is a formatting spec, so

            --outputLeafDir 'preview-%%s'

        where %%s is the original leaf directory node, will prefix each
        final directory containing output with the text 'preview-' which
        can be useful in describing some features of the output set.

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

        [--man]
        Show full help.

        [--synopsis]
        Show brief help.

        [--verbosity <level>]
        Set the app verbosity level. This ranges from 0...<N> where internal
        log messages with a level=<M> will only display if M <= N. In this
        manner increasing the level here can be used to show more and more
        debugging info, assuming that debug messages in the code have been
        tagged with a level.

        [--stats | --statsReverse]
        If specified, return some stats to caller -- summary list ordered
        by directory size (--statsReverse does a reverse sort).

        [--3D]
        A "toy" flag that simply shows the final stats report with an ASCII
        3D effect.

        [--jsonStats]
        If specified, do a JSON dump of the stats.

        [--syslog]
        If specified, prepend output 'log' messages in syslog style.

        [--test <analysisDelayLength[:<type>]>]
        If specified, perform a test/dummy run through the

            - read
            - analyze
            - write

        callbacks. The <analysisDelayLength> denotes time (in seconds)
        to delay in the analysis loop -- useful for testing threading
        performance.

        An optional [:<type>] can be specified.

            :0  - write the 'l_file' to each outputdir, i.e. a simple 'ls'
                  analog
            :1  - write only the number of files analyzed to each outputdir,
                  i.e. a summary.

        For large trees, ':0' can take a significantly longer time than
        ':1'.

Examples
--------

stats
~~~~~

Run on a target tree and output some detail and stats

.. code:: bash

        pftree          --inputDir /var/www/html                                \
                        --printElapsedTime                                      \
                        --stats --verbosity 0

Increasing the ``verbosity`` will produce increasing output on the console. Passing
a ``--json`` will return a highly detailed JSON payload with considerable information.
Passing a ``--jsonStats`` will only return a summary of the final stats on the
filesystem probed. Note that the ``--verbosity`` flag is ignored if ``--json`` or ``--jsonStats`` are also present.

test
~~~~

Run a test down a target tree:

.. code:: bash

        pftree          --inputDir /etc                                         \
                        --outputDir /tmp/test                                   \
                        --verbosity 1 --relativeDir                             \
                        --outputLeafDir 'preview-%%s'                           \
                        --test 0

which will "copy" the input tree to the output, and save a file-ls.txt in each directory where necessary. Note the ``-r`` for 'relative' directory specification and the ``--outputLeafDir`` spec.

_-30-_