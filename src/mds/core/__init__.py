"""纯函数核心层。

**禁止**在本包内 import PyQt6 / picard / httpx —— 由 tests/unit/test_boundaries.py 断言守卫。
本层只做业务规则：归一化、清洗、差异、候选评分、决策编排。
"""
