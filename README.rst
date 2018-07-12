pftree
======

Quick Overview
--------------

-  Create a dictionary representation of a filesystem hierarchy.

Overview
--------

pftree recursively walks down an input directory tree and creates a dictionary representation of the path structure. Each tree "key" has a list of files in that corresponding directory in the filesystem. 

Dependencies
------------

The following dependencies are installed on your host system/python3 virtual env (they will also be automatically installed if pulled from pypi):

-  pfmisc (various misc modules and classes for the pf* family of objects)

Installation
~~~~~~~~~~~~

The best method of installing this script and all of its dependencies is
by fetching it from PyPI

.. code:: bash

        pip3 install pftree



Command line arguments
----------------------

::
        -I|--inputDir <inputDir>
        Input DICOM directory to examine. By default, the first file in this
        directory is examined for its tag information. There is an implicit
        assumption that each <inputDir> contains a single DICOM series.

        -r|--relativeDir
        A flag argument. If passed (i.e. True), then the dictionary key values
        are taken to be relative to the <inputDir>, i.e. the key values
        will not contain the <inputDir>; otherwise the key values will
        contain the <inputDir>.

        -i|--inputFile <inputFile>
        An optional <inputFile> specified relative to the <inputDir>. If 
        specified, then do not perform a directory walk, but convert only 
        this file.

        [-O|--outputDir <outputDir>]
        The directory to contain all output files.

        [--stats | --statsReverse]
        If specified, return some stats to caller -- summary list ordered
        by directory size (--statsReverse does a reverse sort).

        [--json]
        If specified, do a JSON dump of the stats.

        [-x|--man]
        Show full help.

        [-y|--synopsis]
        Show brief help.

        -v|--verbosity <level>
        Set the app verbosity level. 

             -1: No internal output.
              0: All internal output.

Examples
~~~~~~~~

Run on a target tree and output some detail and stats

.. code:: bash

        pftree          -I /var/www/html                \
                        -O /tmp                         \
                        -r                              \
                        --printElapsedTime              \
                        --stats -v -1 --json

