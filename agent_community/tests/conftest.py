"""pytest 共享夹具：把 agent_community 包根（父目录）加入 sys.path。

注意必须 append 到末尾而非 insert(0)：
- dev 根存在 platform\\ 包目录，若置于 sys.path 前部会遮蔽标准库 platform 模块，
  导致 pytest 自身 import platform 即崩溃；
- 标准库 types 同名冲突已通过 types.py -> shared_types.py 重命名解决。
"""
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent  # agent_community 包根
_PARENT = _PKG_ROOT.parent                          # import agent_community 的父目录

if str(_PARENT) not in sys.path:
    sys.path.append(str(_PARENT))
