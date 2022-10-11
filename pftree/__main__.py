#!/usr/bin/env python3
#
# (c) 2017-2022 Fetal-Neonatal Neuroimaging & Developmental Science Center
#                    Boston Children's Hospital
#
#              http://childrenshospital.org/FNNDSC/
#                        dev@babyMRI.org
#

import  pudb
import sys, os
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '../pftree'))

try:
    from    .               import pftree
    from    .               import __pkg, __version__
except:
    from pftree             import pftree
    from __init__           import __pkg, __version__


from    argparse            import RawTextHelpFormatter
from    argparse            import ArgumentParser

import  pfmisc
from    pfmisc._colors      import Colors
from    pfmisc              import other

str_desc = Colors.CYAN + f'''


        __ _
       / _| |
 _ __ | |_| |_ _ __ ___  ___
| '_ \|  _| __| '__/ _ \/ _ \\
| |_) | | | |_| | |  __/  __/
| .__/|_|  \__|_|  \___|\___|
| |
|_|



                            Path-File tree structure

        Recursively walk down an input directory tree and create a  dictionary
        representation of the path structure. Each tree "key" corresponds to a
        dirctory path and the key  "value" is a list  of files in  that corre-
        sponding directory in the filesystem.

                             -- version ''' + \
             Colors.YELLOW + __version__ + Colors.CYAN + ''' --

        The main purpose of this module, other than probing a filesystem tree,
        is to provide some base methods to a caller that:

            * read input files in an input directory
            * analyze these files
            * save resultant files in an output directory

        Callers can override these methods for specific needs/workflows.  This
        module can orchestrate the flow of these core methods in either single
        or multiple threaded modes.

''' + Colors.NO_COLOUR

package_IOcore  = """
        --inputDir <inputDir>                                                   \\
        [--outputDir <outputDir>]                                               \\"""

package_CLIcore = """
        [--fileFilter <someFilter1,someFilter2,...>]                            \\
        [--filteFilterLogic AND|OR]                                             \\
        [--dirFilter <someFilter1,someFilter2,...>]                             \\
        [--dirFilterLogic AND|OR]                                               \\
        [--maxdepth <dirDepth>]                                                 \\
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

package_CLIself     = '''
        [--stats | --statsReverse | --du | --duf]                               \\
        [--3D]                                                                  \\
        [--jsonStats]                                                           \\
        [--syslog]                                                              \\
        [--test <analysisDelayLength[:<type>]>]                                 \\'''

package_argSynopsisSelf = """
        [--stats | --statsReverse | --du | --duf]
        If specified, return some stats to caller. The amount of information
        returned depends on the --verbosity.

        For --stats (and --statsReverse):

            * --verbosity 0: return only a final summary of group statistics
            * --verbosity 1: in addition, return a sorted (by size) list of
                             subdirectories in the search tree
            * --verbosity >1: same as above, but provide probing status updates.
                              NOTE: this incurs a significant performance penalty!

        For --du | --duf

            similar to '--stats' but return directory lists in a fashion similar
            to the GNU 'du' tool. Both of these set default verbosity values so that

            * --du : only provide a summary
            * --duf: provide the (full) sorted list as well

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
"""

package_argSynopsisIO = """

        --inputDir <inputDir>
        Input directory to examine. The downstream nested structure of this
        directory is examined and recreated in the <outputDir>.

        [--outputDir <outputDir>]
        The directory to contain a tree structure identical to the input
        tree structure, and which contains all output files from the
        per-input-dir processing.
"""

package_argSynopsisCore = """

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

        [--overwrite]
        If specified, allow for overwriting of existing files

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
"""

def synopsis(ab_shortOnly = False):
    scriptName = os.path.basename(sys.argv[0])
    shortSynopsis =  '''
    NAME

        pftree - Create a dictionary representation of a filesystem tree.

    SYNOPSIS

        pftree \ ''' + package_IOcore + package_CLIself + package_CLIcore + '''


    BRIEF EXAMPLE

        pftree                                                                  \\
                --inputDir /var/www/html                                        \\
                --outputDir /tmp                                                \\
                --relativeDir                                                   \\
                --printElapsedTime                                              \\
                --stats


    '''

    description =  '''
    DESCRIPTION

        ``pftree`` in and of itself is does not really do any work. It is a
        class that provides the internals for representing file system
        hierarchies in dictionary form.

        As a convenience, however, the ``--stats`` or ``--statsRevers``
        flags do provide a useful analog for sorted directory usage down
        a file system tree.

        Given an ``<inputDir>``, ``pftree`` will perform a recursive walk down
        the directory tree. For each directory that contains files,
        ``pftree`` will create a dictionary key of the directory path,
        and will store a list of filenames for the key value.

        The core the of the class is a tree_analysisApply() method, that
        accepts various kwargs. When called, this method will loop
        over the dictionary, and for each key (i.e. 'path') will execute
        a callback method. This callback is passed the dictionary value
        at that key (i.e. usually just the list of files) as well as
        all the kwargs passed to tree_analysisApply().

    ARGS ''' +  package_argSynopsisIO   + \
                package_argSynopsisCore + \
                package_argSynopsisSelf + '''


    EXAMPLES

    * Run on a target tree and output some detail and stats

        pftree          --inputDir /var/www/html                                \\
                        --printElapsedTime                                      \\
                        --stats --verbosity 0 --json

    which will output only at script conclusion and will log a JSON formatted
    string. Similarly

        pftree          --du --inputDir /var/www/html

    * Run a test down a target tree:

        pftree          --inputDir /etc                                         \\
                        --outputDir /tmp/test                                   \\
                        --verbosity 1 --relativeDir                             \\
                        --outputLeafDir 'preview-%%s'                           \\
                        --test 0

    which will "copy" the input tree to the output, and save a file-ls.txt
    in each directory where necessary. Note  for 'relative' directory
    specification and the ``--outputLeafDir`` spec.

    '''
    if ab_shortOnly:
        return shortSynopsis
    else:
        return shortSynopsis + description


parserIO    = ArgumentParser(description        = 'I/O',
                             formatter_class    = RawTextHelpFormatter,
                             add_help           = False)
parserCore  = ArgumentParser(description        = 'Core',
                             formatter_class    = RawTextHelpFormatter,
                             add_help           = False)
parserSelf  = ArgumentParser(description        = 'Self specific',
                             formatter_class    = RawTextHelpFormatter,
                             add_help           = False)


parserIO.add_argument("--inputDir",
                    help    = "input dir",
                    dest    = 'inputDir',
                    default = '')
parserIO.add_argument("--outputDir",
                    help    = "output image directory",
                    dest    = 'outputDir',
                    default = '.')

parserCore.add_argument("--maxDepth",
                    help    = "max depth, counting from zero, to descend",
                    dest    = 'maxDepth',
                    default = '-1')
parserCore.add_argument("--inputFile",
                    help    = "input file",
                    dest    = 'inputFile',
                    default = '')
parserCore.add_argument("--man",
                    help    = "man",
                    dest    = 'man',
                    action  = 'store_true',
                    default = False)
parserCore.add_argument("--synopsis",
                    help    = "short synopsis",
                    dest    = 'synopsis',
                    action  = 'store_true',
                    default = False)
parserCore.add_argument("--verbosity",
                    help    = "verbosity level for app",
                    dest    = 'verbosity',
                    default = "1")
parserCore.add_argument("--threads",
                    help    = "number of threads for innermost loop processing",
                    dest    = 'threads',
                    default = "0")
parserCore.add_argument("--outputLeafDir",
                    help    = "formatting spec for output leaf directory",
                    dest    = 'outputLeafDir',
                    default = "")
parserCore.add_argument("--relativeDir",
                    help    = "use relative directories",
                    dest    = 'relativeDir',
                    action  = 'store_true',
                    default = False)
parserCore.add_argument("--json",
                    help    = "JSON final return",
                    dest    = 'json',
                    action  = 'store_true',
                    default = False)
parserCore.add_argument("--printElapsedTime",
                    help    = "print program run time",
                    dest    = 'printElapsedTime',
                    action  = 'store_true',
                    default = False)
parserCore.add_argument("--followLinks",
                    help    = "follow symbolic links",
                    dest    = 'followLinks',
                    action  = 'store_true',
                    default = False)
parserCore.add_argument("--overwrite",
                    help    = "allow for overwriting of existing files",
                    dest    = 'overwrite',
                    action  = 'store_true',
                    default = False)
parserCore.add_argument('--version',
                    help    = 'if specified, print version number',
                    dest    = 'b_version',
                    action  = 'store_true',
                    default = False)
parserCore.add_argument("--fileFilter",
                    help    = "a list of comma separated string filters to apply across the input file space",
                    dest    = 'fileFilter',
                    default = '')
parserCore.add_argument("--fileFilterLogic",
                    help    = "the logic to apply across the file filter",
                    dest    = 'fileFilterLogic',
                    default = 'OR')
parserCore.add_argument("--dirFilter",
                    help    = "a list of comma separated string filters to apply across the input dir space",
                    dest    = 'dirFilter',
                    default = '')
parserCore.add_argument("--dirFilterLogic",
                    help    = "the logic to apply across the dir filter",
                    dest    = 'dirFilterLogic',
                    default = 'OR')
parserCore.add_argument("--syslog",
                    help    = "show outputs in syslog style",
                    dest    = 'syslog',
                    action  = 'store_true',
                    default = False)

parserSelf.add_argument("--stats",
                    help    = "show some quick stats",
                    dest    = 'stats',
                    action  = 'store_true',
                    default = False)
parserSelf.add_argument("--statsReverse",
                    help    = "show some quick stats (reverse order)",
                    dest    = 'statsReverse',
                    action  = 'store_true',
                    default = False)
parserSelf.add_argument("--du",
                    help    = "show disk usage in the GNU du style",
                    dest    = 'du',
                    action  = 'store_true',
                    default = False)
parserSelf.add_argument("--duf",
                    help    = "show disk usage in the GNU du style (no console updating)",
                    dest    = 'duf',
                    action  = 'store_true',
                    default = False)
parserSelf.add_argument("--3D",
                    help    = "show table in ASCII 3D",
                    dest    = 'table3D',
                    action  = 'store_true',
                    default = False)
parserSelf.add_argument("--jsonStats",
                    help    = "JSON dump stats",
                    dest    = 'jsonStats',
                    action  = 'store_true',
                    default = False)
parserSelf.add_argument("--test",
                    help    = "perform a test run of the read/analyze/write loop -- arg indicates sleep length in analyze",
                    dest    = 'test',
                    default = '')

def main(argv = None):

    parser  = ArgumentParser(description        = str_desc,
                             formatter_class    = RawTextHelpFormatter,
                             parents            = [parserIO, parserCore, parserSelf])
    args = parser.parse_args()

    if args.man or args.synopsis:
        print(str_desc)
        if args.man:
            str_help     = synopsis(False)
        else:
            str_help     = synopsis(True)
        print(str_help)
        return 1

    if args.b_version:
        print("Name:    %s\nVersion: %s" % (__pkg.name, __version__))
        return 1

    args.str_version    = __version__
    args.str_desc       = synopsis(True)

    try:
        pf_tree             = pftree.pftree(vars(args))
    except:
        pf_tree             = pftree(vars(args))

    # And now run it!
    d_pftree = pf_tree.run(timerStart = True)

    if args.printElapsedTime and not args.json and not args.jsonStats:
        pf_tree.dp.qprint(
                            "Elapsed time = %f seconds" %
                            d_pftree['runTime'], level = 0
                        )

    return 0

if __name__ == "__main__":
    sys.exit(main())