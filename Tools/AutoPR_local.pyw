"""Double-click launcher without a Python console."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_suffix(".py")), run_name="__main__")
