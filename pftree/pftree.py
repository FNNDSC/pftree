# System imports
import      os
import      sys
import      getpass
import      argparse
import      json
import      pprint
import      time

# Project specific imports
import      pfmisc
from        pfmisc._colors      import  Colors
from        pfmisc              import  other
from        pfmisc              import  error

import      pudb

import      threading
from        tqdm                import  tqdm
import      pathlib

try:
    from    .                   import __name__, __version__
except:
    from    __init__            import __name__, __version__

class slog(object):
    """
    A simple class that simply appends to an internal
    'payload' each time it is called and which provides
    some "pretty printing" functionality
    """

    def syslog(self, *args):
        if len(args):
            self.b_syslog = args[0]
        else:
            return self.b_syslog

    def __init__(self, *args, **kwargs):
        self.str_payload    : str   = ""
        self.b_syslog       : bool  = False
        self.str_title      : str   = ""
        self.l_padding      : int   = 2
        self.r_padding      : int   = 2
        self.b_3D           : bool  = False
        self.str_shadow     : str   = ''

    def clear(self):
        self.str_payload    = ""

    def render3D(self):
        self.b_3D           = True
        self.str_shadow     = "█"

    def title_set(self, str_title):
        self.str_title      = str_title

    def __call__(self, astr):
        self.str_payload += str(astr)

    def json_dump(self):
        """
        Dump the payload as a simple JSON object
        """
        return {
            'log': {
                'title':    self.str_title,
                'body':      self.str_payload
            }
        }

    def border_draw(self, **kwargs):

        def title_draw():
            """Draw the title in a BeOS style tab
            """
            nonlocal width
            widthTitle  = len(self.str_title)
            if widthTitle > width:
                self.str_title = self.str_title[0:width-5] + '...'
                widthTitle = len(self.str_title)
            h_len       = widthTitle + self.l_padding  + self.r_padding
            top         = ''.join(['┌'] + ['─' * h_len] + ['┐']) + '\n'
            result      = top                       + \
                            '│'                     + \
                            ' ' * self.l_padding    + \
                                  self.str_title    + \
                            ' ' * self.r_padding    + \
                            '│' + self.str_shadow   + '\n'
            offset      = 2 + self.l_padding + len(self.str_title) + self.r_padding
            return result, offset

        for k,v in kwargs.items():
            if k == 'left_padding'  :   self.l_padding   = v
            if k == 'right_padding' :   self.r_padding   = v

        msg_list    = self.str_payload.split('\n')
        msg_list    = [ x.replace('\t', '       ') for x in msg_list]
        l_c0        = [x.count('\x1b[0;')*7 for x in msg_list]
        l_c1        = [x.count('\x1b[1;')*7 for x in msg_list]
        l_nc        = [x.count('\x1b[0m')*4 for x in msg_list]
        l_offset    = [x + y + z for x, y, z in zip(l_c0, l_c1, l_nc)]
        msg_listNoEsc   = [(len(x) - w) for x, w in zip(msg_list, l_offset)]
        width       = max(msg_listNoEsc)
        h_len       = width + self.l_padding + self.r_padding
        top_bottom  = ''.join(['+'] + ['-' * h_len] + ['+']) + '\n'
        top         = ''.join(['┌'] + ['─' * h_len] + ['┐']) + '\n'
        bottom      = ''.join(['└'] + ['─' * h_len] + ['┘']) + self.str_shadow + '\n'
        botShadow   = ''.join([' '] + [self.str_shadow] * (h_len+2)) + '\n'

        result      = ''

        if len(self.str_title):
            result, offset = title_draw()
            top     = '├' + top[1:offset-1] + '┴' + top[offset:]

        result     += top

        for m, l in zip(msg_list, msg_listNoEsc):
            spaces   = h_len - l
            l_spaces = ' ' * self.l_padding
            r_spaces = ' ' * (spaces - self.l_padding)
            result += '│' + l_spaces + m + r_spaces + '│' + self.str_shadow +' \n'

        result += bottom
        if self.b_3D: result += botShadow
        return result

    def __repr__(self):
        return self.str_payload

class pftree(object):
    """
    A class that constructs a dictionary represenation of the paths in a filesystem.

    The "keys" are the paths (relative to some root dir), and the "value" is a list
    of files in that path.

    Workflow logic:

        * tree_probe()              - return a list of files and dirs down a tree
        * tree_construct()          - construct the "input" and "output" dictionary:
                                        -- <keys> are directory path names
                                        -- <val> list of files in <keys> path
        * tree_analysisApply        - apply arbitrary analysis on the files in each
                                      directory of the "input" tree. Results are usually
                                      saved to "output" tree, but can in fact
                                      be saved to "input" tree instead (if for example
                                      some filter operation on the input tree files
                                      is required). See the method itself for calling
                                      syntax and **kwargs behavior.

    """

    def declare_selfvars(self):
        """
        A block to declare self variables
        """
        self._dictErr = {
            'inputDirFail'   : {
                'action'        : 'trying to check on the input directory, ',
                'error'         : 'directory not found. This is a *required* input',
                'exitCode'      : 1},
            'inputReadCallback' : {
                'action'        : 'checking on the status of the inputReadCallback return, ',
                'error'         : 'no boolean "status" was found. This is a *required* return key',
                'exitCode'      : 2},
            'analysisCallback'  : {
                'action'        : 'checking on the status of the analysisCallback return, ',
                'error'         : 'no boolean "status" was found. This is a *required* return key',
                'exitCode'      : 3},
            'outputWriteCallback' : {
                'action'        : 'checking on the status of the outputWriteCallback return, ',
                'error'         : 'no boolean "status" was found. This is a *required* return key',
                'exitCode'      : 4}
            }

        #
        # Object desc block
        #
        self.__name__                   = __name__
        self.str_version                = __version__

        # Object containing this class
        self.within                     = None

        # Thread number
        self.numThreads                 = 1

        # Directory and filenames
        self.str_inputDir               = ''
        self.str_inputFile              = ''
        self.str_outputDir              = ''
        self.d_inputTree                = {}
        self.d_inputTreeCallback        = {}
        self.d_outputTree               = {}
        self.str_outputLeafDir          = ''
        self.maxdepth                   = -1

        # Flags
        self.b_persistAnalysisResults   = False
        self.b_relativeDir              = False
        self.b_stats                    = False
        self.b_statsReverse             = False
        self.b_jsonStats                = False
        self.b_json                     = False
        self.b_test                     = False
        self.b_followLinks              = False
        self.str_sleepLength            = ''
        self.f_sleepLength              = 0.0
        self.testType                   = 0

        self.dp                         = None
        self.log                        = None
        self.tic_start                  = 0.0
        self.pp                         = pprint.PrettyPrinter(indent=4)
        self.verbosityLevel             = 1
        self.debugLevel                 = 0

    def checkFor_tests(self) -> bool:
        """Checks the internal self.str_sleepLength value
        that is used to trigger tests. The CLI `--test` flag
        might be set by a descendent or sibling module and
        hence cause issues if interpreted by this class.

        This method attempts some very rudimentary checks on
        the `--test` flag (and hence the self.str_sleepLength).

        Returns:
            bool: tests checked
        """
        b_status    : bool  = False
        if isinstance(self.str_sleepLength, str):
            if len(self.str_sleepLength):
                b_status    = True
                l_test  = self.str_sleepLength.split(':')
                self.str_sleepLength    = l_test[0]
                if len(l_test) == 2:
                    self.testType   = int(l_test[1])
                try:
                    self.f_sleepLength      = float(self.str_sleepLength)
                    self.b_test             = True
                except:
                    self.b_test             = False
        return b_status

    def __init__(self, *args, **kwargs):

        # pudb.set_trace()
        self.declare_selfvars()
        self.args                       = args[0]
        self.str_desc                   = self.args['str_desc']
        if len(self.args):
            kwargs  = {**self.args, **kwargs}

        for pkey in ['du', 'duf']:
            if pkey not in self.args.keys(): self.args[pkey] = False
        if self.args['du']:
            self.verbosityLevel = 2
            self.debugLevel     = 2
        if self.args['duf']:
            self.verbosityLevel = 0
            self.debugLevel     = 0

        for key, value in kwargs.items():
            if key == 'inputDir':       self.str_inputDir       = value
            if key == 'maxDepth':       self.maxdepth           = int(value)
            if key == 'inputFile':      self.str_inputFile      = value
            if key == 'outputDir':      self.str_outputDir      = value
            if key == 'verbosity':      self.verbosityLevel     = int(value)
            if key == 'threads':        self.numThreads         = int(value)
            if key == 'relativeDir':    self.b_relativeDir      = bool(value)
            if key == 'stats':          self.b_stats            = bool(value)
            if key == 'statsReverse':   self.b_statsReverse     = bool(value)
            if key == 'jsonStats':      self.b_jsonStats        = bool(value)
            if key == 'json':           self.b_json             = bool(value)
            if key == 'followLinks':    self.b_followLinks      = bool(value)
            if key == 'test':           self.str_sleepLength    = value
            if key == 'outputLeafDir':  self.str_outputLeafDir  = value

        self.checkFor_tests()

        # Set logging
        self.dp                        = pfmisc.debug(
                                            verbosity   = self.verbosityLevel,
                                            within      = self.__name__,
                                            syslog      = self.args['syslog']
                                            )
        self.log                       = pfmisc.Message()
        self.log.syslog(True)

        if not len(self.str_inputDir): self.str_inputDir = '.'

    def toConsole(self) -> bool:
        """A simple check on CLI flag patterning to resolve whether or not
        to actually generate console output. This output needs to return
        false if any json related flag has been indicated since any non-
        json "noise" could corrupt any app that wants to only consume
        json data from this module.

        Essentially, the method will return True, unless either a
        --json or --jsonStats has been specified

        Returns:
            bool: True if OK to print to console
        """

        b_toConsole :   bool    = True

        if self.verbosityLevel:
            for noConsole in ['jsonStats', 'json']:
                if noConsole in self.args.keys():
                    b_toConsole     = b_toConsole and not self.args[noConsole]
        else:
            b_toConsole = False

        return b_toConsole

    def simpleProgress_show(self, index, total, *args):
        str_pretext = ""
        if len(args):
            str_pretext = args[0] + ":"
        f_percent   = index/total*100
        str_num     = "[%3d/%3d: %6.2f%%] " % (index, total, f_percent)
        str_bar     = "*" * int(f_percent)
        self.dp.qprint("%s%s%s" % ( str_pretext, str_num, str_bar),
                                    stackDepth  = 2,
                                    level       = 2)

    @staticmethod
    # For finish path walking.
    # Partially from https://stackoverflow.com/questions/229186/os-walk-without-digging-into-directories-below
    # Edited.
    def walklevel(path, depth = -1, **kwargs):
        """It works just like os.walk, but you can pass it a level parameter
        that indicates how deep the recursion will go.
        If depth is -1 (or less than 0), the full depth is walked.
        """
        # if depth is negative, just walk
        if depth < 0:
            for root, dirs, files in os.walk(path, **kwargs):
                yield root, dirs, files

        # path.count works because is a file has a "/" it will show up in the list
        # as a ":"
        path    = path.rstrip(os.path.sep)
        num_sep = path.count(os.path.sep)
        for root, dirs, files in os.walk(path, **kwargs):
            yield root, dirs, files
            num_sep_this = root.count(os.path.sep)
            if num_sep + depth <= num_sep_this:
                del dirs[:]

    def tree_probe(self, **kwargs):
        """
        Perform an os walk down a file system tree, starting from
        a **kwargs identified 'root', and return lists of files and
        directories found.

        kwargs:
            root    = '/some/path'

        return {
            'status':   True,
            'l_dir':    l_dirs,
            'l_files':  l_files
        }

        """

        def nextSpinner(b_cursorToNextLine):
            """Provide a rotating spinner to indicate activity by using a closure.

            Returns:
                inner : inner function
            """
            spinner = '\\|/-'
            pos     = 0
            def inner(b_cursorToNextLine):
                nonlocal pos, spinner
                if pos>=len(spinner): pos = 0
                if self.toConsole():
                    self.dp.qprint('Probing filesystem... {}'.format(spinner[pos]), end = '')
                    if not b_cursorToNextLine:
                        self.dp.qprint('\r', end = '', syslog = self.args['syslog'])
                    else:
                        self.dp.qprint('\n', end = '', syslog = self.args['syslog'])
                pos += 1
                return inner
            return inner

        def path_shorten(str_path, length = 80) -> str:
            """Shorten a Path string

            Returns:
                string : a shortened path
            """
            if length < 0:
                length = os.get_terminal_size().columns + length
            if len(str_path) > length:
                l_parts = list(pathlib.PurePath(str_path).parts)
                l_copy  = l_parts.copy()
                max     = len(l_parts)
                offset  = -1
                center  = max // 2
                while len(str_path) > length:
                    offset += 1
                    l_shorten = [i % (max + 1) for i in range(  center - offset,
                                                    center + offset + 1)]
                    for prt in l_shorten: l_copy[prt] = '...'
                    str_path    = str(pathlib.PurePath(*l_copy))
            return str_path

        def elements_flash(l_el, debugLevel):
            """
            Flash elements in the passed list at the debugLevel
            """
            if self.toConsole():
                for el in l_el:
                    self.dp.qprint('%s (%d)\033[K\r' % \
                            (path_shorten(el, - len(str(len(l_el))) - 4), len(l_el)),
                            level   = debugLevel,
                            end     = '',
                            syslog  = self.args['syslog'])


        str_topDir          = "."
        l_dirs              = []
        l_files             = []
        b_status            = False
        str_path            = ''
        l_dirsHere          = []
        l_filesHere         = []
        b_cursorToNextLine  = False

        for k, v in kwargs.items():
            if k == 'root':  str_topDir  = v

        if int(self.verbosityLevel) >= 2:
            b_cursorToNextLine = True
        spinner             = nextSpinner(b_cursorToNextLine)
        for root, dirs, files in pftree.walklevel(str_topDir,
                                                  self.maxdepth,
                                                  followlinks = self.b_followLinks):
            b_status = True
            if self.verbosityLevel >= 2: spinner(b_cursorToNextLine)
            str_path = root.split(os.sep)
            if dirs:
                l_dirsHere = [root + '/' + x for x in dirs]
                l_dirs.append(l_dirsHere)
                if self.verbosityLevel >= 2: elements_flash(l_dirsHere, 2)
            if files:
                l_filesHere = [root + '/' + y for y in files]
                if len(self.str_inputFile):
                    l_hit = [s for s in l_filesHere if self.str_inputFile in s]
                    if l_hit:
                        l_filesHere = l_hit
                    else:
                        l_filesHere = []
                if l_filesHere:
                    l_files.append(l_filesHere)
                    if self.verbosityLevel >= 3: elements_flash(l_filesHere, 3)
            if self.toConsole() and self.verbosityLevel >=2:
                self.dp.qprint("\033[A" * 1,
                                end     = '',
                                syslog  = self.args['syslog'],
                                level   = 2 )
        if self.toConsole() and self.verbosityLevel >= 2:
            self.dp.qprint('Probing complete!              ', level = 1)
        return {
            'status':   b_status,
            'l_dir':    l_dirs,
            'l_files':  l_files
        }

    def tree_construct(self, *args, **kwargs):
        """
        Processes the <l_files> list of files from the tree_probe()
        and builds the input/output dictionary structures.

        Optionally execute a constructCallback function, and return
        results
        """
        l_files                 = []
        d_constructCallback     = {}
        fn_constructCallback    = None
        d_probe                 = {}
        l_range                 = []

        for k, v in kwargs.items():
            if k == 'l_files':           l_files                 = v
            if k == 'constructCallback': fn_constructCallback    = v
            if k == 'd_probe':           d_probe                 = v

        if d_probe: l_files     = d_probe['l_files']
        index   = 1
        total   = len(l_files)
        if int(self.verbosityLevel) and self.toConsole():
            l_range     = tqdm(l_files, desc = ' Constructing tree')
        else:
            l_range     = l_files
        for l_series in l_range:
            str_path    = os.path.dirname(l_series[0])
            l_series    = [ os.path.basename(i) for i in l_series]
            # self.simpleProgress_show(index, total)
            self.d_inputTree[str_path]  = l_series
            if fn_constructCallback:
                kwargs['path']          = str_path
                d_constructCallback     = fn_constructCallback(l_series, **kwargs)
                self.d_inputTreeCallback[str_path]  = d_constructCallback
            self.d_outputTree[str_path] = ""
            index += 1
        return {
            'status':                   True,
            'd_constructCallback':      d_constructCallback,
            'totalNumberOfAllSeries':   index,
            'd_probe':                  d_probe
        }

    def FS_filter(self, at_data, *args, **kwargs) -> dict:
        """
        Apply a filter to the string space of file and directory
        representations.

        The purpose of this method is to reduce the original space of

                        "<path>": [<"filesToProcess">]

        to only those paths and files that are relevant to the operation being
        performed. Two filters are understood, a `fileFilter` that filters
        filenames that match any of the passed search substrings from the CLI
        `--fileFilter`, and a`dirFilter` that filters directories containing
        any of the passed `--dirFilter` substrings.

        The effect of these filters is hierarchical. First, the `fileFilter`
        is applied across the space of files for a given directory path. By
        default, the files are subject to a logical OR operation across the
        comma separated filter argument. Thus, a `fileFilter` of "png,jpg,body"
        will filter all files that have the substrings of "png" OR "jpg" OR
        "body" in their filenames. This logical operation can be set with
        "--fileFilterLogic AND" to use AND instead. In such a case, a filter
        of "aparc,mgz" will filter all files that contain "aparc" AND "mgz"
        in their filenames. If no `fileFilter` has been set, that all files
        are passed through.

        Next, if a `dirFilter` has been specified, the current string path
        corresponding to the filenames being filtered is considered. A logical
        OR (default) is applied over the space of dir filters and the path
        (and files) are passed through if the dirname contains the filter
        string. Set this operation to AND with `--dirFilterLogic AND`.

        Regardless of the constituent file/dir filter logic operation, the
        relationship between the fileList and dirList is always AND.

        Thus, a `dirFilter` of "100307,100556" and a fileFilter of "png,jpg"
        will reduce the space of files to process to ONLY files that have
        an ancestor directory of "100307" OR "100556" AND that contain either
        the string "png" OR "jpg" in their file names.
        """

        b_status    : bool      = True
        l_file      : list      = []
        l_dirHits   : list      = []
        l_dir       : list      = []
        str_path    : str       = at_data[0]
        al_file     : list      = at_data[1]

        if len(self.args['fileFilter']):
            if self.args['fileFilterLogic'].upper() == 'OR':
                al_file     = [x                                            \
                            for y in self.args['fileFilter'].split(',')     \
                                for x in al_file if y in x]
            else:
                for y in self.args['fileFilter'].split(','):
                    al_file = [x for x in al_file if y in x]

        if len(self.args['dirFilter']):
            l_dirHits   = [str_path                                         \
                            for y in self.args['dirFilter'].split(',')      \
                                if y in str_path]
            if self.args['dirFilterLogic'].upper()  == 'AND':
                for y in self.args['dirFilter'].split(','):
                    l_dirHits = [x for x in l_dirHits if y in x]
            if len(l_dirHits):
                # Remove any duplicates in the l_dirHits: duplicates can occur
                # if the tokens in the filter expression map more than once
                # into the leaf node in the <str_path>, as a path that is
                #
                #               /some/dir/in/the/space/1234567
                #
                # and a search filter on the dirspace of "123,567"
                [l_dir.append(x) for x in l_dirHits if x not in l_dir]
            else:
                # If no dir hits for this dir, then we zero out the
                # file filter
                al_file = []

        if len(al_file):
            al_file.sort()
            l_file      = al_file
            b_status    = True
        else:
            self.dp.qprint( "No valid files to analyze found in path %s!" %
                            str_path, comms = 'warn', level = 5)
            l_file      = None
            b_status    = False
        return {
            'status':   b_status,
            'l_file':   l_file
        }

    def filterFileHitList(self) -> dict:
        """
        Entry point for filtering the file filter list
        at each directory node.
        """
        d_filterFileHitList = self.tree_process(
                        inputReadCallback       = None,
                        analysisCallback        = self.FS_filter,
                        outputWriteCallback     = None,
                        applyResultsTo          = 'inputTree',
                        applyKey                = 'l_file',
                        persistAnalysisResults  = True
        )
        # We also need to filter the self.d_inputTreeCallBack to only
        # contain the hits now in the self.d_inputTree
        self.d_inputTreeCallback = {k :self.d_inputTreeCallback[k]
                                        for k in self.d_inputTree}

        return d_filterFileHitList

    @staticmethod
    def sizeof_fmt(num, suffix='B'):
        for unit in ['','k','M','G','T','P','E','Z']:
            if abs(num) < 1024.0:
                return "%3.1f%s%s" % (num, unit, suffix)
            num /= 1024.0
        return "%.1f%s%s" % (num, 'Yi', suffix)

    @staticmethod
    def dirsize_get(l_filesWithoutPath, **kwargs):
        """
        Sample callback that determines a directory size.
        """

        str_path    = ""
        for k,v in kwargs.items():
            if k == 'path': str_path = v

        d_ret   = {}
        l_size  = []
        size    = 0
        for f in l_filesWithoutPath:
            str_f   = '%s/%s' % (str_path, f)
            if not os.path.islink(str_f):
                try:
                    size += os.path.getsize(str_f)
                except:
                    pass
        str_size    = pftree.sizeof_fmt(size)

        return {
            'status':           True,
            'diskUsage_raw':    size,
            'diskUsage_human':  str_size
        }


    def tree_process(self, *args, **kwargs):
        """

        kwargs:

            inputReadCallback       = callback to perform inputIO (read)
            analysisCallback        = callback to perform analysis
            outputWriteCallback     = callback to perform outputIO (write)
            applyResultsTo          = 'inputTree'|'outputTree'
            applyKey                = <arbitrary key in analysis dictionary>
            persistAnalysisResults  = True|False

        This method performs the actual work of this class. Operations are
        divided into three callback groups:

            * Input reading
            * Actual processing
            * Output writing

        The method will loop over all the "paths" in <inputTree>, and for each
        "path" call the inputRead/dataAnalysis/outputWrite callbacks in order.

        If this pftree object is initialized as multi-threaded, only the
        dataAnalysis callback is actually threaded. The read and write
        file IO callbacks are run sequentially for efficiency (threaded
        file IO is horribly inefficient and actually degrades in linear
        proportion to the number of threads).

        The results of the analysis are typically stored in the corresponding
        path in the <outputTree> (unless 'persistAnalysisResults' == False);
        however, results can also be applied to the <inputTree> (see below).

        The results of the dataAnalysisCallback are usually stored in the
        outputTree at a path corresponding to the inputTree. If

            kwargs:     applyTo     = 'inputTree'

        is passed, then the results are saved to the <inputTree> instead.

        Furthermore, if

            kwargs:     applyKey    = 'someKey'

        is passed, then only the results of 'someKey' in the returned
        dictionary are saved.

        Thus, an enclosing class can call this method to, for example, filter
        the list of files at each path location by:

            pftree.tree_process(
                                ...
                        analysisCallback        =  fn_filterFileList,
                        applyResultsTo          = 'inputTree',
                        applyKey                = 'files'
            )

        will apply the callback function, fn_filterFileList and return some
        filtered list in its return dictionary at key == 'files'. This
        dictionary value is stored in the <inputTree>.

        Finally, if either

            self.b_peristOutputResults  = True

        or

            kwargs: peristOutputResults = True

        Then this method will save all output results at each location in the
        <outputTree> path. This can become prohibitively large in memory if
        operations are applied that seek to save large results at each
        directory (like dicom anon, for example). In that case, passing/setting
        a <False> will not save results in the <outputTree> (other than a
        boolean status) and will immediately do a callback on the results
        to process them. In this case, a kwargs

            kwags:  outputcallback      = self.fn_outputcallback

        is called on the dictionary result of the analysiscallback method. The
        result of this outputcallback is saved to the <outputTree> instead.

        Note that threading the analysisCallback will effectively result in
        output results being persistent across the entire tree (since the execution
        loop finishes each step sequenitally: all input IO, thread analysis, all
        output IO).

        """
        str_applyResultsTo          = ""
        str_applyKey                = ""
        fn_inputReadCallback        = None
        fn_analysisCallback         = None
        fn_outputWriteCallback      = None
        b_persistAnalysisResults    = False
        d_tree                      = self.d_outputTree
        str_processType             = ''
        dret_inputSet               = {}
        dret_analyze                = {}
        dret_outputSet              = {}
        filesRead                   = 0
        filesAnalyzed               = 0
        filesSaved                  = 0
        str_desc                    = ""

        def thread_batch(l_threadFunc, outerLoop, innerLoop, offset):
            """
            Fire up a set of threads and wait for them to join
            the main execution flow
            """
            start   = 0
            join    = 0
            il      = lambda f, i, o, l : f + i + o * l
            for t_o in range(0, outerLoop):
                for t_i in range(0, innerLoop):
                    idx = il(offset, t_i, t_o, innerLoop)
                    l_threadFunc[idx].start()
                    start += 1
                    # self.dp.qprint('Started thread %d' % start)

                for t_i in range(0, innerLoop):
                    idx = il(offset, t_i, t_o, innerLoop)
                    l_threadFunc[idx].join()
                    join += 1
                    # self.dp.qprint('Join set on thread %d' % join)

            return start

        def inputSet_read(path, data):
            """
            The core canonical component that reads file sets
            from specific "leaf" nodes in the <inputDir>.
            """
            nonlocal    filesRead
            nonlocal    index
            nonlocal    d_tree
            nonlocal    fn_inputReadCallback

            d_read = fn_inputReadCallback((path, data), **kwargs)

            if 'status' in d_read.keys():
                d_tree[path]    = d_read
                if 'filesRead' in d_read.keys():
                    filesRead   += d_read['filesRead']
            else:
                self.dp.qprint(
                    "The inputReadCallback callback did not return a 'status' value!",
                    comms = 'error',
                    level = 0
                )
                error.fatal(self, 'inputReadCallback',  drawBox = True)
            return d_read

        def analysis_do(path, data, index, **kwargs):
            nonlocal    filesAnalyzed
            nonlocal    d_tree
            nonlocal    fn_analysisCallback

            d_analysis          = fn_analysisCallback((path, d_tree[path]), **kwargs)

            if 'status' in d_analysis.keys():
                if d_analysis['status']:
                    # Analysis was successful
                    if len(str_applyKey):
                        d_tree[path]    = d_analysis[str_applyKey]
                    else:
                        d_tree[path]    = d_analysis
                    if 'filesAnalyzed' in d_analysis.keys():
                        filesAnalyzed       += d_analysis['filesAnalyzed']
                    elif 'l_file' in d_analysis.keys():
                        filesAnalyzed   += len(d_analysis['l_file'])
                else:
                    # If status was false, mark this key/path as
                    # None
                    d_tree[path]    = None
            else:
                self.dp.qprint(
                    "The analysis callback did not return a 'status' value!",
                    comms = 'error',
                    level = 0
                )
                error.fatal(self, 'analysisCallback',  drawBox = True)
            return d_analysis

        def tree_removeDeadBranches():
            """
            It is possible that an analysis_do() run will in fact determine
            that a given branch in the tree being processed does not in fact
            have any files for processing. In that case, it sets a 'None' to
            the corresponding dictionary entry in d_tree.

            This method simply removes all those None branches from the
            d_tree dictionary -- creating a new copy of d_tree in the process
            """
            nonlocal d_tree
            d_tree = { k : v for k, v in d_tree.items() if v}
            # By creating a new binding for 'd_tree', we have effectively
            # severed the connection back to the original dictionary.
            # We now need to copy this d_tree to the self.d_inputTree
            # self.d_outputTree structures
            self.d_inputTree    = d_tree
            self.d_outputTree   = self.d_inputTree.copy()

        def outputSet_write(path, data):
            """
            The core canonical component that writes file sets
            to specific leaf nodes in the <outputDir>.
            """
            nonlocal    filesSaved
            nonlocal    index
            nonlocal    d_tree
            nonlocal    fn_analysisCallback
            nonlocal    b_persistAnalysisResults

            # self.simpleProgress_show(index, total, '%s:%s' %
            #     ('%25s' % threading.currentThread().getName(),
            #      '%25s' % fn_outputWriteCallback.__name__)
            # )

            if len(self.str_outputLeafDir):
                (dirname, basename) = os.path.split(path)
                str_format  = '\'%s\'' % self.str_outputLeafDir
                new_basename = str_format + ' % basename'
                str_eval    = eval(new_basename)
                path        = '%s/%s' % (dirname, str_eval)

            d_output        = fn_outputWriteCallback(
                ( '%s/%s' % (self.str_outputDir, path), data), **kwargs
            )

            if 'status' in d_output.keys():
                if not b_persistAnalysisResults:
                    d_tree[path]    = d_output
                filesSaved          += d_output['filesSaved']
            else:
                self.dp.qprint(
                    "The outputWriteCallback callback did not return a 'status' value!",
                    comms = 'error',
                    level = 0
                )
                error.fatal(self, 'outputWriteCallback',  drawBox = True)
            return d_output

        def status_determine():
            """
            Return the status as a function of the individual status values
            of each of input/analyze/output.
            """
            b_status        = False
            b_statusInput   = True
            b_statusAnalyze = True
            b_statusOutput  = True
            nonlocal dret_inputSet
            nonlocal dret_analyze
            nonlocal dret_outputSet
            nonlocal fn_inputReadCallback
            nonlocal fn_analysisCallback
            nonlocal fn_outputWriteCallback

            if fn_inputReadCallback:
                if 'status' in dret_inputSet.keys():
                    b_statusInput   = dret_inputSet['status']
            if fn_analysisCallback:
                if 'status' in dret_analyze.keys():
                    b_statusAnalyze = dret_analyze['status']
            if fn_outputWriteCallback:
                if 'status' in dret_outputSet.keys():
                    b_statusOutput  = dret_outputSet['status']

            b_status = b_statusInput and b_statusAnalyze and b_statusOutput
            return {
                'status': b_status
            }

        def loop_nonThreaded():
            """
            Loop over the problem domain space and process
            the three main components (read, analysis, write)
            in sequential order.

            The status for each operation (read, analysis, write)
            is the logical OR over the status results of each individual
            directory node. Thus, a False for a specific operation
            indicates that ALL operations over the space of directory
            nodes failed.
            """
            nonlocal index, total
            nonlocal d_tree
            nonlocal fn_inputReadCallback
            nonlocal fn_analysisCallback
            nonlocal fn_outputWriteCallback
            nonlocal dret_inputSet
            nonlocal dret_analyze
            nonlocal dret_outputSet
            nonlocal str_desc

            b_analyzeStatusHist:    bool = False
            b_inputStatusHist:      bool = False
            b_outputStatusHist:     bool = False

            if int(self.verbosityLevel) and self.toConsole():
                iterator        = tqdm( self.d_inputTree.items(),
                                    desc = str_desc)
            else:
                iterator        = self.d_inputTree.items()

            for path, data in iterator:
                dret_inputSet   = {}
                dret_analyze    = {}
                dret_outputSet  = {}
                # Read (is sometimes skipped) / Analyze / Write (also sometimes skipped)
                if fn_inputReadCallback:
                    dret_inputSet       = inputSet_read(path, data)
                    try:
                        b_inputStatusHist   = b_inputStatusHist or dret_inputSet['status']
                    except:
                        pass
                if fn_analysisCallback:
                    try:
                        dret_analyze    = analysis_do(path, d_tree[path], index)
                    except:
                        dret_analyze['status']  = False
                        self.dp.qprint("Analysis failed", comms = 'error')
                    try:
                        b_analyzeStatusHist = b_analyzeStatusHist or dret_analyze['status']
                    except:
                        pass
                if fn_outputWriteCallback:
                    if 'status' in dret_analyze.keys():
                        if dret_analyze['status']:
                            dret_outputSet  = outputSet_write(path, d_tree[path])
                            try:
                                b_outputStatusHist  = b_outputStatusHist or dret_outputSet['status']
                            except:
                                pass
                index += 1
            dret_inputSet['status']     = b_inputStatusHist
            dret_analyze['status']      = b_analyzeStatusHist
            dret_outputSet['status']    = b_outputStatusHist
            tree_removeDeadBranches()

        def loop_threaded():
            """
            Loop over the problem domain space and process
            the three main components (read, analysis, write)
            in thread-friendly order.

            This means performing *all* the reads sequentially
            (non threaded), followed by the analysis threaded into
            batches, followed by the writes all sequentially.
            """
            nonlocal index, total
            nonlocal d_tree
            nonlocal fn_inputReadCallback
            nonlocal fn_analysisCallback
            nonlocal fn_outputWriteCallback
            nonlocal dret_inputSet
            nonlocal dret_analyze
            nonlocal dret_outputSet
            nonlocal str_desc

            def thread_createOnFunction(path, data, str_namePrefix, fn_thread):
                """
                Simply create a thread function and return it.
                """
                nonlocal index
                ta  = threading.Thread(
                            name    = '%s-%04d.%d' % (str_namePrefix, index, self.numThreads),
                            target  = fn_thread,
                            args    = (path, data, index),
                            kwargs  = kwargs
                )
                return ta

            def threadsInBatches_run(l_threadAnalysis):
                """
                Run threads in batches of self.numThreads
                and also handle any remaining threads.
                """
                index               = 1
                if self.numThreads > total:
                    self.numThreads = total
                threadFullLoops     = int(total / self.numThreads)
                threadRem           = total % self.numThreads
                alreadyRunCount = thread_batch(
                                        l_threadAnalysis,
                                        threadFullLoops,
                                        self.numThreads,
                                        0)
                nextRunCount    =  thread_batch(
                                        l_threadAnalysis,
                                        1,
                                        threadRem,
                                        alreadyRunCount)

            if int(self.verbosityLevel) and self.toConsole():
                iterator        = tqdm( self.d_inputTree.items(),
                                    desc = str_desc)
            else:
                iterator        = self.d_inputTree.items()

            # Read
            if fn_inputReadCallback:
                index = 1
                for path, data in iterator:
                    dret_inputSet   = inputSet_read(path, data)
                    # filesRead       += dret_inputSet['filesRead']
                    index += 1

            # Analyze
            if fn_analysisCallback:
                index               = 1
                l_threadAnalysis    = []
                for path, data in iterator:
                    l_threadAnalysis.append(thread_createOnFunction(
                                                    path, data,
                                                    'analysisThread',
                                                    # t_analyze
                                                    analysis_do
                                            )
                    )
                    index += 1

                # And now batch them in groups
                threadsInBatches_run(l_threadAnalysis)
                tree_removeDeadBranches()
            # Write
            if fn_outputWriteCallback:
                index   = 1
                for path, data in iterator:
                    dret_outputSet  = outputSet_write(path, d_tree[path])
                    # filesSaved      += dret_outputSet['filesSaved']
                    index += 1

        for k, v in kwargs.items():
            if k == 'inputReadCallback':        fn_inputReadCallback        = v
            if k == 'analysisCallback':         fn_analysisCallback         = v
            if k == 'outputWriteCallback':      fn_outputWriteCallback      = v
            if k == 'applyResultsTo':           str_applyResultsTo          = v
            if k == 'applyKey':                 str_applyKey                = v
            if k == 'persistAnalysisResults':   b_persistAnalysisResults    = v

        if fn_inputReadCallback:    str_desc = ' Reading      tree'
        if fn_analysisCallback:     str_desc = ' Analyzing    tree'
        if fn_outputWriteCallback:  str_desc = ' Saving  new  tree'
        if fn_inputReadCallback and fn_analysisCallback and fn_outputWriteCallback:
            str_desc =                         'Read/Analyze/Write'

        if str_applyResultsTo == 'inputTree':
            d_tree          = self.d_inputTree

        index               = 1
        total               = len(self.d_inputTree.keys())
        l_threadAnalysis    = []

        if not self.numThreads:
            loop_nonThreaded()
            str_processType     = "Not threaded"
        else:
            loop_threaded()
            str_processType     = "Threaded"

        # pudb.set_trace()

        return {
            'status':               status_determine()['status'],
            'processType':          str_processType,
            'fileSetsProcessed':    index,
            'filesRead':            filesRead,
            'filesAnalyzed':        filesAnalyzed,
            'filesSaved':           filesSaved,
            'd_inputCallback':      dret_inputSet,
            'd_analyzeCallback':    dret_analyze,
            'd_outputCallback':     dret_outputSet
        }

    def tree_analysisOutput(self, *args, **kwargs):
        """
        An optional method for looping over the <outputTree> and
        calling an outputcallback on the analysis results at each
        path.

        Only call this if self.b_persisAnalysisResults is True.
        """
        fn_outputcallback           = None
        for k, v in kwargs.items():
            if k == 'outputcallback':           fn_outputcallback           = v
        index   = 1
        total   = len(self.d_inputTree.keys())
        for path, d_analysis in self.d_outputTree.items():
            self.simpleProgress_show(index, total)
            self.dp.qprint("Processing analysis results in output: %s" % path)
            d_output        = fn_outputcallback((path, d_analysis), **kwargs)
        return {
            'status':   True
        }

    @staticmethod
    def delete_last_line():
        "Use this function to delete the last line in the STDOUT"
        #cursor up one line
        sys.stdout.write('\x1b[1A')
        #delete last line
        sys.stdout.write('\x1b[2K')

    def stats_compute(self, *args, **kwargs):
        """
        Simply loop over the internal dictionary and
        echo the list size at each key (i.e. the number
        of files).
        """
        totalElements   = 0
        totalKeys       = 0
        totalSize       = 0
        l_stats         = []
        d_report        = {}
        str_report      = ""
        l_range         = []

        if int(self.verbosityLevel) and self.toConsole():
            l_range     = tqdm(sorted(self.d_inputTreeCallback.items(),
                            key         = lambda kv: (kv[1]['diskUsage_raw']),
                            reverse     = self.b_statsReverse),
                            desc = ' Processing  stats')
        else:
            l_range     = sorted(self.d_inputTreeCallback.items(),
                            key         = lambda kv: (kv[1]['diskUsage_raw']),
                            reverse     = self.b_statsReverse)

        for k, v in l_range:
            try:
                if not self.args['du'] and not self.args['duf']:
                    str_report  += "files: %5d│ raw_size: %12d│ human_size: %8s│ dir: %s\n" % (\
                            len(self.d_inputTree[k]),
                            self.d_inputTreeCallback[k]['diskUsage_raw'],
                            self.d_inputTreeCallback[k]['diskUsage_human'],
                            k)
                else:
                    str_report += '%-10s%s\n' % (
                        self.d_inputTreeCallback[k]['diskUsage_human'], k)
            except:
                pass
            d_report = {
                'files':            len(self.d_inputTree[k]),
                'diskUsage_raw':    self.d_inputTreeCallback[k]['diskUsage_raw'],
                'diskUsage_human':  self.d_inputTreeCallback[k]['diskUsage_human'],
                'path':             k
            }
            l_stats.append(d_report)
            totalElements   += len(v)
            totalKeys       += 1
            totalSize       += self.d_inputTreeCallback[k]['diskUsage_raw']
        str_totalSize_human = self.sizeof_fmt(totalSize)
        return {
            'status':           True,
            'report':           str_report,
            'dirs':             totalKeys,
            'files':            totalElements,
            'totalSize':        totalSize,
            'totalSize_human':  str_totalSize_human,
            'l_stats':          l_stats,
            'runTime':          other.toc()
        }

    def inputReadCallback(self, *args, **kwargs):
        """
        Test for inputReadCallback

        This method does not actually "read" the input files,
        but simply returns the passed file list back to
        caller
        """
        b_status    = True
        filesRead   = 0

        for k, v in kwargs.items():
            if k == 'l_file':   l_file      = v
            if k == 'path':     str_path    = v

        if len(args):
            at_data         = args[0]
            str_path        = at_data[0]
            l_file          = at_data[1]

        self.dp.qprint("reading (in path %s):\n%s" %
                            (str_path,
                            self.pp.pformat(l_file)),
                            level = 5)
        filesRead   = len(l_file)

        if not len(l_file): b_status = False

        return {
            'status':           b_status,
            'l_file':           l_file,
            'str_path':         str_path,
            'filesRead':        filesRead
        }

    def inputAnalyzeCallback(self, *args, **kwargs):
        """
        Test method for inputAnalzeCallback

        This method loops over the passed number of files,
        and optionally "delays" in each loop to simulate
        some analysis. The delay length is specified by
        the '--test <delay>' flag.

        """
        b_status            = False
        filesRead           = 0
        filesAnalyzed       = 0

        for k, v in kwargs.items():
            if k == 'filesRead':    d_DCMRead   = v
            if k == 'path':         str_path    = v

        if len(args):
            at_data         = args[0]
            str_path        = at_data[0]
            d_read          = at_data[1]

        b_status        = True
        self.dp.qprint("analyzing:\n%s" %
                                self.pp.pformat(d_read['l_file']),
                                level = 5)
        if int(self.f_sleepLength):
            self.dp.qprint("sleeping for: %f" % self.f_sleepLength, level = 5)
            time.sleep(self.f_sleepLength)
        filesAnalyzed   = len(d_read['l_file'])

        return {
            'status':           b_status,
            'filesAnalyzed':    filesAnalyzed,
            'l_file':           d_read['l_file']
        }

    def outputSaveCallback(self, at_data, **kwargs):
        """
        Test method for outputSaveCallback

        Simply writes a file in the output tree corresponding
        to the number of files in the input tree.
        """
        path                = at_data[0]
        d_outputInfo        = at_data[1]
        other.mkdir(self.str_outputDir)
        filesSaved          = 0
        other.mkdir(path)
        if not self.testType:
            str_outfile         = '%s/file-ls.txt'      % path
        else:
            str_outfile         = '%s/file-count.txt'   % path

        with open(str_outfile, 'w') as f:
            self.dp.qprint("saving: %s" % (str_outfile), level = 5)
            if not self.testType:
                f.write('%s`' % self.pp.pformat(d_outputInfo['l_file']))
            else:
                f.write('%d\n' % d_outputInfo['filesAnalyzed'])
        filesSaved += 1

        return {
            'status':       True,
            'outputFile':   str_outfile,
            'filesSaved':   filesSaved
        }


    def test_run(self, *args, **kwargs):
        """
        Perform a test run of the read/analyze/write loop
        (thread aware).
        """

        self.b_relativeDir              = True
        d_test = self.tree_process(
                inputReadCallback       = self.inputReadCallback,
                analysisCallback        = self.inputAnalyzeCallback,
                outputWriteCallback     = self.outputSaveCallback,
                persistAnalysisResults  = False
        )
        return d_test

    def env_check(self):
        """
        Simple environment check routine.
        """
        b_status    :   bool    = True
        str_error   :   str     = "no error"
        if not os.path.exists(self.str_inputDir):
            b_status    = False
            if self.toConsole():
                error.warn(self, 'inputDirFail', exitToOS = True, drawBox = True)
            str_error   = 'error captured while accessing input directory'
        return {
            'status'    : b_status,
            'error'     : str_error
        }

    @staticmethod
    def unpack(d : dict, *keys):
        return tuple(d[k] for k in keys)

    def run(self, *args, **kwargs):
        """
        Probe the input tree and print.
        """

        def filters_show():
            """
            Show the filters used
            """
            log         = slog()
            log.title_set('Filters applied')
            if self.args['table3D']: log.render3D()
            log('Input directory:       %s\n' % self.str_inputDir)
            log('Output directory:      %s\n' % self.str_outputDir)
            for filter in ['file', 'dir']:
                log('%sFilter:            ' % filter)
                sl_ffilter  = ['%s %s' % (x, self.args['%sFilterLogic' % filter]) \
                                for x in self.args['%sFilter' % filter].split(',')]
                str_ffilter = ' '.join(sl_ffilter)
                sl_ffilter  = str_ffilter.split()
                str_ffilter = ' '.join(sl_ffilter[:-1])
                log('%s\n ' % str_ffilter)
            return log

        def stats_process():
            """
            Call the dir/files stats processing
            """
            nonlocal d_stats, b_status
            log         = slog()
            d_stats     = self.stats_compute()
            if self.toConsole() or self.args['duf'] or self.args['du']:
                self.dp.qprint(d_stats['report'], level = self.debugLevel)
            slog_filter = filters_show()
            log.title_set('Size statistics')
            if self.args['table3D']: log.render3D()
            log('Total size (raw):        %d\n' % d_stats['totalSize']          )
            log('Total size (friendly):   {:,}\n'.format(d_stats['totalSize'])  )
            log('Total size (human):      %s\n' % d_stats['totalSize_human']    )
            log('Total files:             %s\n' % d_stats['files']              )
            log('Total dirs:              %s\n' % d_stats['dirs']               )
            log('Total runtime:           %5.3f s' % other.toc()                )
            b_status    = b_status and d_stats['status']
            return {
                'status':       b_status,
                'filterLog':    slog_filter,
                'bodyLog':      log
            }

        def tree_resolveRoot():
            """
            Set the 'rootDir' for the tree structure. This is either a
            '.' indicating a relative tree, or the inputDir
            """
            nonlocal str_rootDir
            if self.b_relativeDir:
                os.chdir(self.str_inputDir)
                str_rootDir     = '.'
            else:
                str_rootDir     = self.str_inputDir
            return str_rootDir

        def timer_startIfNeeded():
            """
            Determine if the timer should start
            """
            nonlocal b_timerStart
            for k, v in kwargs.items():
                if k == 'timerStart':   b_timerStart    = bool(v)
            if b_timerStart:
                other.tic()

        def postProcess_check() -> dict:
            """
            Once a tree has been constructed, run some
            in-line post processing filtering and other
            operations as desired.
            """
            nonlocal d_test, b_status, d_filter, d_stats

            if len(self.args['fileFilter']) or len(self.args['dirFilter']):
                d_filter    = self.filterFileHitList()
                b_status    = d_filter['status']
            if self.b_test:
                d_test      = self.test_run(*args, **kwargs)
                b_status    = b_status and d_test['status']
            if  self.b_stats or self.b_statsReverse or \
                self.b_jsonStats or self.args['du'] or self.args['duf']:
                d_stats     = stats_process()
                b_status    = b_status and d_stats['status']
                self.verbosityLevel = 1
                if self.toConsole():
                    if not self.args['du'] and not self.args['duf']:
                        print(d_stats['filterLog'].border_draw())
                        print(d_stats['bodyLog'].border_draw())
                    elif self.args['du'] or self.args['duf']:
                        print(d_stats['bodyLog'])
                else:
                    d_stats['filterLog']    = d_stats['filterLog'].json_dump()
                    d_stats['bodyLog']      = d_stats['bodyLog'].json_dump()

            return {
                'status':   b_status,
                'filter':   d_filter,
                'test':     d_test,
                'stats':    d_stats
            }

        b_status        = True
        d_probe         = {}
        d_tree          = {}
        d_stats         = {}
        d_post          = {}
        str_error       = ''
        b_timerStart    = False
        d_test          = {}
        d_env           = {}
        d_filter        = {}
        str_rootDir     = ''

        timer_startIfNeeded()
        b_status, str_error = self.unpack(self.env_check(), 'status', 'error')

        if b_status:
            str_origDir = os.getcwd()
            d_tree      = self.tree_construct(
                d_probe             = self.tree_probe(root = tree_resolveRoot()),
                constructCallback   = self.dirsize_get
            )
            b_status    = d_tree['status']
            d_post      = postProcess_check()
            if self.b_jsonStats:
                print(json.dumps(d_post['stats'], indent = 4, sort_keys = True))

            if self.b_relativeDir:
                os.chdir(str_origDir)

        d_ret = {
            'status':       b_status,
            'd_tree':       d_tree,
            'd_stats':      d_stats,
            'd_test':       d_test,
            'd_post':       d_post,
            'str_error':    str_error,
            'runTime':      other.toc()
        }

        if self.b_json:
            print(json.dumps(d_ret, indent = 4, sort_keys = True))

        return d_ret
