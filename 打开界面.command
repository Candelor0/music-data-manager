#!/bin/bash
# 音乐数据管家 —— 只看不写的预览窗口
# 双击本文件即可打开。想关掉窗口，直接点窗口左上角的红点。
# 这个窗口不会修改任何音乐文件（代码层面就没有改文件的能力）。

cd "$(dirname "$0")" || exit 1

echo "正在打开「音乐数据管家」预览窗口…"

if [ -x ./.venv/bin/python ]; then
  exec ./.venv/bin/python -m mds.cli gui
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run mds gui
fi

echo ""
echo "❌ 没找到运行环境。请先在本目录执行一次：uv sync"
echo ""
read -r -p "按回车键关闭…"
exit 1
