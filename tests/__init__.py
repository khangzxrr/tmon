import sys
import unittest

# Reading a config file needs tomllib (Python 3.11+); on 3.10 only the file-less paths are tested.
needs_tomllib = unittest.skipIf(sys.version_info < (3, 11), "config files need Python 3.11 (tomllib)")
