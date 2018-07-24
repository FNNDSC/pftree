pftree
======

Quick Overview
--------------

-  Create a dictionary representation of a filesystem hierarchy.
-  Optionally report some stats on the hierarchy (esp size of each directory).

Overview
--------

pftree recursively walks down an input directory tree and creates a dictionary representation of the path structure. Each tree "key" has a list of files in that corresponding directory in the filesystem. 

pftree in and of itself is does not really do any work. It is a class that provides the internals for representing file system hierarchies in dictionary form.

As a convenience, however, the '--stats' or '--statsReverse' do provide a useful analog for sorted directory usage down a file system tree.

Given an <inputDir>, pftree will perform a recursive walk down the directory tree. For each directory that contains files, `pftree` will create a dictionary key of the directory path, and will store a list of filenames for the key value.

The core the of the class is a <tree_analysisApply> method, that accepts various kwargs. When called, this method will loop over the dictionary, and for each key (i.e. 'path') will execute a callback method. This callback is passed the dictionary value at that key (i.e. usually just the list of files) as well as all the kwargs passed to <tree_analysisApply>.

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

.. code:: html

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

        [-t|--threads <numThreads>]
        If specified, break the innermost analysis loop into <numThreads>
        threads. Please note the following caveats:

            * Only thread if you have a high CPU analysis loop. Since
              most of the operations of this module will entail reading
              and writing DICOM files, and since these operations are 
              the bulk of the execution time, adding threading will not
              really help.

            * Threading will change the nature of the innermost looping
              across the problem domain, with the result that *all* of the
              problem data will be read into memory! That means all of 
              DICOMs across all of the subdirs! In non-threading mode,
              only DICOMs from a single directory at a time are read
              and then discarded.

        [--json]
        If specified, do a JSON dump of the stats.

        [-x|--man]
        Show full help.

        [-y|--synopsis]
        Show brief help.

        -v|--verbosity <level>
        Set the app verbosity level. 

            0: No internal output;
            1: Most important internal output, i.e. sorted stat results;
            2: As with level '1' but with simpleProgress bar;
            3: As with level '2' but with list of input dirs/files;

Examples
~~~~~~~~

Run on a target tree and output some detail and stats

.. code:: bash

        pftree          -I /var/www/html                \
                        --printElapsedTime              \
                        --stats -v 0 --json

which will output only at script conclusion and will log a JSON formatted string.
