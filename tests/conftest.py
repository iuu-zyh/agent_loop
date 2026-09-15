"""tests/conftest.py — 保证 agent_loop 以「包名」解析（路径约定与 scripts/*.py 相同）。

项目根 F:\agent_loop 自身即 agent_loop 包（__init__.py 在根），父目录必须在
sys.path 上，`import agent_loop` 才会解析到包而非根下 agent_loop.py 模块文件。
"""
import sys
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
