"""打包成 exe 时的入口脚本（PyInstaller 从这里启动）。

为什么单独写一个入口，而不是直接打 `mds/cli.py`：
    双击 exe 时**没有任何命令行参数**，而 `mds` 的 CLI 是子命令式的
    （`mds gui` / `mds doctor` / …），不带参数会报"用法错误"。
    这里做一件事：**没带参数就默认开界面**。

所以：
    双击 `音乐数据管家.exe`            → 打开界面
    `音乐数据管家.exe doctor`          → 命令行自检（排错用）
    `音乐数据管家.exe run "D:\\Music"` → 一键跑完整流程
"""

from __future__ import annotations

import sys


def main() -> int:
    from mds.cli import main as cli_main

    argv = sys.argv[1:]
    if not argv:  # 双击进来的
        argv = ["gui"]
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
