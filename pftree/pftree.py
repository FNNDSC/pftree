# System imports
import      os
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

    _dictErr = {
        'inputDirFail'   : {
            'action'        : 'trying to check on the input directory, ',
            'error'         : 'directory not found. This is a *required* input',
            'exitCode'      : 1}
        }


    def declare_selfvars(self):
        """
        A block to declare self variables
        """
        self._dictErr = {
            'inputDirFail'   : {
                'action'        : 'trying to check on the input directory, ',
                'error'         : 'directory not found. This is a *required* input',
                'exitCode'      : 1}
            }

        #
        # Object desc block
        #
        self.str_desc                   = ''
        self.__name__                   = "pftree"

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

        # Flags
        self.b_persistAnalysisResults   = False
        self.b_relativeDir              = False
        self.b_stats                    = False
        self.b_statsReverse             = False
        self.b_jsonStats                = False
        self.b_json                     = False
        self.b_test                     = False
        self.str_sleepLength            = ''
        self.f_sleepLength              = 0.0

        self.dp                         = None
        self.log                        = None
        self.tic_start                  = 0.0
        self.pp                         = pprint.PrettyPrinter(indent=4)
        self.verbosityLevel             = 1

    def __init__(self, **kwargs):

        # pudb.set_trace()
        self.declare_selfvars()

        for key, value in kwargs.items():
            if key == 'inputDir':       self.str_inputDir       = value
            if key == 'inputFile':      self.str_inputFile      = value
            if key == 'outputDir':      self.str_outputDir      = value
            if key == 'verbosity':      self.verbosityLevel     = int(value)
            if key == 'threads':        self.numThreads         = int(value)
            if key == 'relativeDir':    self.b_relativeDir      = bool(value)
            if key == 'stats':          self.b_stats            = bool(value)
            if key == 'statsReverse':   self.b_statsReverse     = bool(value)
            if key == 'jsonStats':      self.b_jsonStats        = bool(value)
            if key == 'json':           self.b_json             = bool(value)
            if key == 'test':           self.str_sleepLength    = value
            if key == 'outputLeafDir':  self.str_outputLeafDir  = value

        if len(self.str_sleepLength):
            try:
                self.f_sleepLength      = float(self.str_sleepLength)
                self.b_test             = True
            except:
                self.b_test             = False

        # Set logging
        self.dp                        = pfmisc.debug(    
                                            verbosity   = self.verbosityLevel,
                                            within      = self.__name__
                                            )
        self.log                       = pfmisc.Message()
        self.log.syslog(True)

        if not len(self.str_inputDir): self.str_inputDir = '.'

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

        str_topDir  = "."
        l_dirs      = []
        l_files     = []
        b_status    = False
        str_path    = ''
        l_dirsHere  = []
        l_filesHere = []

        for k, v in kwargs.items():
            if k == 'root':  str_topDir  = v

        for root, dirs, files in os.walk(str_topDir):
            b_status = True
            str_path = root.split(os.sep)
            if dirs:
                l_dirsHere = [root + '/' + x for x in dirs]
                l_dirs.append(l_dirsHere)
                self.dp.qprint('Appending dirs to search space:\n', level = 3)
                self.dp.qprint("\n" + self.pp.pformat(l_dirsHere),  level = 3)
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
                self.dp.qprint('Appending files to search space:\n', level = 3)
                self.dp.qprint("\n" + self.pp.pformat(l_filesHere),  level = 3)
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
        for k, v in kwargs.items():
            if k == 'l_files':           l_files                 = v
            if k == 'constructCallback': fn_constructCallback    = v

        index   = 1
        total   = len(l_files)
        for l_series in l_files:
            str_path    = os.path.dirname(l_series[0])
            l_series    = [ os.path.basename(i) for i in l_series]
            self.simpleProgress_show(index, total)
            self.d_inputTree[str_path]  = l_series
            if fn_constructCallback:
                kwargs['path']          = str_path
                d_constructCallback     = fn_constructCallback(l_series, **kwargs)
                self.d_inputTreeCallback[str_path]  = d_constructCallback
            self.d_outputTree[str_path] = ""
            index += 1
        return {
            'status':                   True,
            'd_constructCalback':       d_constructCallback,   
            'totalNumberOfAllSeries':   index
        }

    @staticmethod
    def sizeof_fmt(num, suffix='B'):
        for unit in ['','Ki','Mi','Gi','Ti','Pi','Ei','Zi']:
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
        dret_analysis               = {}
        dret_outputSet              = {}
        filesRead                   = 0
        filesAnalyzed               = 0
        filesSaved                  = 0

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

            self.simpleProgress_show(index, total, '%s:%s' % 
                ('%25s' %threading.currentThread().getName(), 
                 '%25s' % fn_inputReadCallback.__name__)
            )
            d_read = fn_inputReadCallback(
                ('%s/%s' % (self.str_inputDir, path), data), **kwargs
            )
            d_tree[path]    = d_read
            if 'filesRead' in d_read.keys():
                filesRead   += d_read['filesRead']
            return d_read

        def analysis_do(path, data, index, **kwargs):
            nonlocal    filesAnalyzed
            nonlocal    d_tree
            nonlocal    fn_analysisCallback

            self.simpleProgress_show(index, total, '%s:%s' % 
                ('%25s' % threading.currentThread().getName(), 
                 '%25s' % fn_analysisCallback.__name__)
            )
            d_analysis          = fn_analysisCallback(
                ('%s/%s' % (self.str_inputDir, path), d_tree[path]), **kwargs
            )
            if len(str_applyKey):
                d_tree[path]    = d_analysis[str_applyKey]
            else:
                d_tree[path]    = d_analysis
            if 'filesAnalyzed' in d_analysis.keys():                
                filesAnalyzed       += d_analysis['filesAnalyzed']
            elif 'l_file' in d_analysis.keys():
                filesAnalyzed   += len(d_analysis['l_file'])
            return d_analysis

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

            self.simpleProgress_show(index, total, '%s:%s' % 
                ('%25s' % threading.currentThread().getName(), 
                 '%25s' % fn_outputWriteCallback.__name__)
            )

            if len(self.str_outputLeafDir):
                (dirname, basename) = os.path.split(path)
                str_format  = '\'%s\'' % self.str_outputLeafDir
                new_basename = str_format + ' % basename'
                str_eval    = eval(new_basename)
                path        = '%s/%s' % (dirname, str_eval)

            d_output        = fn_outputWriteCallback(
                ( '%s/%s' % (self.str_outputDir, path), data), **kwargs
            )
            if not b_persistAnalysisResults:
                d_tree[path]    = d_output
            filesSaved          += d_output['filesSaved']
            return d_output

        def loop_nonThreaded():
            """
            Loop over the problem domain space and process
            the three main components (read, analysis, write)
            in sequential order.
            """
            nonlocal index, total
            nonlocal d_tree
            nonlocal fn_inputReadCallback
            nonlocal fn_analysisCallback
            nonlocal fn_outputWriteCallback
            nonlocal dret_inputSet
            nonlocal dret_analysis
            nonlocal dret_outputSet

            for path, data in self.d_inputTree.items():
                # Read (is sometimes skipped) / Analyze / Write
                if fn_inputReadCallback:    dret_inputSet   = inputSet_read(path, data)
                if fn_analysisCallback:     dret_analyze    = analysis_do(path, d_tree[path], index)
                if fn_outputWriteCallback:  dret_outputSet  = outputSet_write(path, d_tree[path])
                index += 1

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
            nonlocal dret_analysis
            nonlocal dret_outputSet

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

            # Read
            if fn_inputReadCallback:
                index = 1
                for path, data in self.d_inputTree.items():
                    dret_inputSet   = inputSet_read(path, data)                    
                    # filesRead       += dret_inputSet['filesRead']
                    index += 1

            # Analyze
            if fn_analysisCallback:
                index               = 1
                l_threadAnalysis    = []
                for path, data in self.d_inputTree.items():
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

            # Write
            if fn_outputWriteCallback:
                index   = 1
                for path, data in self.d_inputTree.items():
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
            'status':               True,
            'processType':          str_processType,
            'fileSetsProcessed':    index,
            'filesRead':            filesRead,
            'filesAnalyzed':        filesAnalyzed,
            'filesSaved':           filesSaved
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

        for k, v in sorted(self.d_inputTreeCallback.items(), 
                            key         = lambda kv: (kv[1]['diskUsage_raw']),
                            reverse     = self.b_statsReverse):
            str_report  = "files: %5d; raw size: %12d; human size: %8s; %s" % (\
                    len(self.d_inputTree[k]), 
                    self.d_inputTreeCallback[k]['diskUsage_raw'], 
                    self.d_inputTreeCallback[k]['diskUsage_human'], 
                    k)
            d_report = {
                'files':            len(self.d_inputTree[k]),
                'diskUsage_raw':    self.d_inputTreeCallback[k]['diskUsage_raw'],
                'diskUsage_human':  self.d_inputTreeCallback[k]['diskUsage_human'],
                'path':             k
            }
            self.dp.qprint(str_report, level = 1)
            l_stats.append(d_report)
            totalElements   += len(v)
            totalKeys       += 1
            totalSize       += self.d_inputTreeCallback[k]['diskUsage_raw']
        str_totalSize_human = self.sizeof_fmt(totalSize)
        return {
            'status':           True,
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
            'filesAnalyzed':    filesAnalyzed
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
        str_outfile         = '%s/output.txt' % path

        with open(str_outfile, 'w') as f:
            self.dp.qprint("saving: %s" % (str_outfile), level = 5)
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

    def run(self, *args, **kwargs):
        """
        Probe the input tree and print.
        """
        b_status        = True
        d_probe         = {}
        d_tree          = {}
        d_stats         = {}
        str_error       = ''
        b_timerStart    = False
        d_test          = {}

        for k, v in kwargs.items():
            if k == 'timerStart':   b_timerStart    = bool(v)

        if b_timerStart:
            other.tic()

        if not os.path.exists(self.str_inputDir):
            b_status    = False
            self.dp.qprint(
                    "input directory either not specified or does not exist.", 
                    comms = 'error'
            )
            error.warn(self, 'inputDirFail', exitToOS = True, drawBox = True)
            str_error   = 'error captured while accessing input directory'

        if b_status:
            str_origDir = os.getcwd()
            if self.b_relativeDir:
                os.chdir(self.str_inputDir)
                str_rootDir     = '.'
            else:
                str_rootDir     = self.str_inputDir

            d_probe     = self.tree_probe(      
                root    = str_rootDir
            )
            b_status    = b_status and d_probe['status']
            d_tree      = self.tree_construct(  
                l_files             = d_probe['l_files'],
                constructCallback   = self.dirsize_get
            )
            b_status    = b_status and d_tree['status']

            if self.b_test:
                d_test      = self.test_run(*args, **kwargs)
                b_status    = b_status and d_test['status']
            else:
                if self.b_stats or self.b_statsReverse:
                    d_stats     = self.stats_compute()
                    self.dp.qprint('Total size (raw):   %d' % d_stats['totalSize'],         level = 1)
                    self.dp.qprint('Total size (human): %s' % d_stats['totalSize_human'],   level = 1)
                    self.dp.qprint('Total files:        %s' % d_stats['files'],             level = 1)
                    self.dp.qprint('Total dirs:         %s' % d_stats['dirs'],              level = 1)
                    b_status    = b_status and d_stats['status']

            if self.b_jsonStats:
                print(json.dumps(d_stats, indent = 4, sort_keys = True))

            if self.b_relativeDir:
                os.chdir(str_origDir)

        d_ret = {
            'status':       b_status,
            'd_probe':      d_probe,
            'd_tree':       d_tree,
            'd_stats':      d_stats,
            'd_test':       d_test,
            'str_error':    str_error,
            'runTime':      other.toc()
        }

        if self.b_json:
            print(json.dumps(d_ret, indent = 4, sort_keys = True))

        return d_ret
        