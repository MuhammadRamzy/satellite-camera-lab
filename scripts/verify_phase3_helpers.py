"""Load reusable Phase 2 verification without naming a Python module with a dash."""
import importlib.util
from pathlib import Path


def load_phase2():
    spec = importlib.util.spec_from_file_location('phase2_verifier', Path(__file__).with_name('verify-phase2.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
