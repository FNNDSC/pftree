import sys

# Make sure we are running python3.5+
if 10 * sys.version_info[0]  + sys.version_info[1] < 35:
    sys.exit("Sorry, only Python 3.5+ is supported.")

from setuptools import setup

def readme():
    with open('README.rst') as f:
        return f.read()

setup(
      name             =   'pftree',
      version          =   '1.6.8',
      description      =   'Utility to create dict representations of file system trees.',
      long_description =   readme(),
      author           =   'FNNDSC',
      author_email     =   'dev@babymri.org',
      url              =   'https://github.com/FNNDSC/pftree',
      packages         =   ['pftree'],
      install_requires =   ['pfmisc', 'pudb'],
      #test_suite       =   'nose.collector',
      #tests_require    =   ['nose'],
      scripts          =   ['bin/pftree'],
      license          =   'MIT',
      zip_safe         =   False
)
