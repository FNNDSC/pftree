# System imports
import      os
import      getpass
import      argparse
import      json
import      pprint

# Project specific imports
import      pfmisc
from        pfmisc._colors      import  Colors
from        pfmisc              import  other
from        pfmisc              import  error

import      pudb

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

        # Directory and filenames
        self.str_inputDir               = ''
        self.str_inputFile              = ''
        self.str_outputDir              = ''
        self.d_inputTree                = {}
        self.d_outputTree               = {}

        # Flags
        self.b_persistAnalysisResults   = False
        self.b_relativeDir              = False
        self.b_stats                    = False
        self.b_statsReverse             = False
        self.b_json                     = False

        self.dp                         = None
        self.log                        = None
        self.tic_start                  = 0.0
        self.pp                         = pprint.PrettyPrinter(indent=4)
        self.verbosityLevel             = -1

    def __init__(self, **kwargs):

        # pudb.set_trace()
        self.declare_selfvars()

        for key, value in kwargs.items():
            if key == "inputDir":       self.str_inputDir   = value
            if key == "inputFile":      self.str_inputFile  = value
            if key == "outputDir":      self.str_outputDir  = value
            if key == 'verbosity':      self.verbosityLevel = int(value)
            if key == 'relativeDir':    self.b_relativeDir  = bool(value)
            if key == 'stats':          self.b_stats        = bool(value)
            if key == 'statsReverse':   self.b_statsReverse = bool(value)
            if key == 'json':           self.b_json         = bool(value)

        # Set logging
        self.dp                        = pfmisc.debug(    
                                            verbosity   = self.verbosityLevel,
                                            level       = 0,
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
        str_num     = "[%3d/%3d: %5.2f%%] " % (index, total, f_percent)
        str_bar     = "*" * int(f_percent)
        self.dp.qprint("%s%s%s" % (str_pretext, str_num, str_bar))

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
                self.dp.qprint('Appending dirs to search space:\n')
                self.dp.qprint("\n" + self.pp.pformat(l_dirsHere))
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
                self.dp.qprint('Appending files to search space:\n')
                self.dp.qprint("\n" + self.pp.pformat(l_filesHere))
        return {
            'status':   b_status,
            'l_dir':    l_dirs,
            'l_files':  l_files
        }

    def tree_construct(self, *args, **kwargs):
        """
        Processes the <l_files> list of files from the tree_probe()
        and builds the input/output dictionary structures.
        """
        l_files = []
        for k, v in kwargs.items():
            if k == 'l_files':  l_files         = v
        index   = 1
        total   = len(l_files)
        for l_series in l_files:
            str_path    = os.path.dirname(l_series[0])
            self.simpleProgress_show(index, total, 'tree_construct')
            self.d_inputTree[str_path]  = l_series
            self.d_outputTree[str_path] = ""
            index += 1
        return {
            'status':           True,
            'seriesNumber':     index
        }

    def tree_analysisApply(self, *args, **kwargs):
        """

        kwargs:

            analysiscallback        = self.fn_filterFileList
            outputcallback          = self.fn_outputprocess
            applyResultsTo          = 'inputTree'|'outputTree'
            applyKey                = <arbitrary key in analysis dictionary>
            persistAnalysisResults  = True|False

        Loop over all the "paths" in <inputTree> and process the file list
        contained in each "path", optionally also calling an outputcallback
        to store results as part of the analysis loop.

        The results of the analysis are typically stored in the corresponding
        path in the <outputTree> (unless 'persistAnalysisResults' == False); 
        however, results can also be applied to the <inputTree> (see below).

        The 'self.within' object is called on a method

            self.within.callbackfunc(<list_files>)

        that applies some analysis to the list of files provided to the method.
        This method must return a dictionary. Typically this dictionary is
        saved to the <outputTree> at the corresponding path location of the
        <inputTree>. If 

            kwargs:     applyTo     = 'inputTree'

        is passed, then the results are saved to the <inputTree> instead. 
        
        Furthermore, if 

            kwargs:     applyKey    = 'someKey'

        is passed, then only the results of 'someKey' in the returned 
        dictionary are saved.

        Thus, an enclosing class can call this method to, for example, filter
        the list of files at each path location by:

            pftree.tree_analysisApply(  
                        analysiscallback    = self.fn_filterFileList,
                        applyResultsTo      = 'inputTree',
                        applyKey            = 'files'
            )

        will apply the callback function, self.fn_filterFileList and return some
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

        """
        str_applyResultsTo          = ""
        str_applyKey                = ""
        fn_analysiscallback         = None
        fn_outputcallback           = None
        b_persistAnalysisResults    = False
        d_tree                      = self.d_outputTree
        for k, v in kwargs.items():
            if k == 'analysiscallback':         fn_analysiscallback         = v
            if k == 'outputcallback':           fn_outputcallback           = v
            if k == 'applyResultsTo':           str_applyResultsTo          = v
            if k == 'applyKey':                 str_applyKey                = v
            if k == 'persistAnalysisResults':   b_persistAnalysisResults    = v
        
        if str_applyResultsTo == 'inputTree': 
            d_tree          = self.d_inputTree

        index   = 1
        total   = len(self.d_inputTree.keys())
        for path, data in self.d_inputTree.items():
            self.simpleProgress_show(index, total, fn_analysiscallback.__name__)
            d_analysis          = fn_analysiscallback(data, **kwargs)
            if len(str_applyKey):
                d_tree[path]    = d_analysis[str_applyKey]
            else:
                d_tree[path]    = d_analysis
            if fn_outputcallback:
                self.simpleProgress_show(index, total, fn_outputcallback.__name__)
                d_output        = fn_outputcallback(d_analysis, **kwargs)
            if not b_persistAnalysisResults:
                d_tree[path]    = d_output
            index += 1
        return {
            'status':   True
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
            d_output        = fn_outputcallback(d_analysis, **kwargs)
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
        l_stats         = []

        for k, v in sorted(self.d_inputTree.items(), 
                            key         = lambda kv: len(kv[1]),
                            reverse     = self.b_statsReverse):
            self.dp.qprint("%000d: %s" % (len(v), k))
            l_stats.append(["%000d: %s" % (len(v), k)])
            totalElements   += len(v)
            totalKeys       += 1
        return {
            'status':   True,
            'dirs':     totalKeys,
            'files':    totalElements,
            'l_stats':  l_stats
        }

    def run(self, *args, **kwargs):
        """
        Probe the input tree and print.
        """
        b_status    = True
        d_probe     = {}
        d_tree      = {}
        d_stats     = {}

        if not os.path.exists(self.str_inputDir):
            b_status    = False
            self.dp.qprint(
                    "input directory either not specified or does not exist.", 
                    comms = 'error'
            )
            error.warn(self, 'inputDirFail', exitToOS = True, drawBox = True)

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
                l_files = d_probe['l_files']
            )
            b_status    = b_status and d_tree['status']
            if self.b_stats or self.b_statsReverse:
                d_stats     = self.stats_compute()
                b_status    = b_status and d_stats['status']

            if self.b_json:
                print(json.dumps(d_stats, indent = 4, sort_keys = True))

            if self.b_relativeDir:
                os.chdir(str_origDir)

        return {
            'status':   b_status,
            'd_probe':  d_probe,
            'd_tree':   d_tree,
            'd_stats':  d_stats
        }
        