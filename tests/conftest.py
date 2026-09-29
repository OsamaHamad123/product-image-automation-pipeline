import os
import sys

# The pipeline modules live at the repository root, one level above tests/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
