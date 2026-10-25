"""CPU fixtures execute real scorers with parser/grounding/generation IO isolated."""
import ast
import copy
import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = Path(__file__).resolve().parents[3] / "experiment" / "evaluations" / "taskchain"
