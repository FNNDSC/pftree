import sys

# Make sure we are running python3.5+
if 10 * sys.version_info[0]  + sys.version_info[1] < 35:
    sys.exit("Sorry, only Python 3.5+ is supported.")

from setuptools import setup

def readme():
    with open('README.rst') as f:
        return f.read()

setup(
      name             =   'pfdicomtag',
      version          =   '1.0.0',
      description      =   '(Python) utility to extract meta data from trees of organized DICOM files.',
      long_description =   readme(),
      author           =   'FNNDSC',
      author_email     =   'dev@babymri.org',
      url              =   'https://github.com/FNNDSC/med2image',
      packages         =   ['pfdicomtag'],
      install_requires =   ['pydicom', 'numpy', 'matplotlib', 'pillow', 'pfmisc'],
      #test_suite       =   'nose.collector',
      #tests_require    =   ['nose'],
      scripts          =   ['bin/pfdicomtag'],
      license          =   'MIT',
      zip_safe         =   False
)
