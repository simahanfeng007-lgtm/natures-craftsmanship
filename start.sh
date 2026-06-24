#!/bin/bash
# 天工造物 v2 - 临渊者 Linux CLI 启动脚本
# 用法: ./start.sh [run_agent.py 参数...]
# 示例: ./start.sh --once "你好" --api-key sk-xxx --model deepseek-chat

set -e

JIAO_BEN_LUJING="$(cd "$(dirname "$0")" && pwd)"
HOU_DUAN="$JIAO_BEN_LUJING/resources/backend"
YUN_XING_SHI="$HOU_DUAN/../backend_runtime/python"

# 找 Python（优先项目自带，回退系统）
if [ -f "$YUN_XING_SHI/python3" ]; then
    PYTHON="$YUN_XING_SHI/python3"
elif [ -f "$YUN_XING_SHI/python" ]; then
    PYTHON="$YUN_XING_SHI/python"
else
    PYTHON="python3"
fi

# 环境变量
export PYTHONPATH="$HOU_DUAN:$PYTHONPATH"
export TIANGONG_JIA="${TIANGONG_JIA:-$HOME/.tiangong}"

# 默认工作区
if [ -z "$TIANGONG_WORKSPACE" ]; then
    export TIANGONG_WORKSPACE="$HOME/Desktop"
fi

exec "$PYTHON" "$HOU_DUAN/run_agent.py" --workspace "$TIANGONG_WORKSPACE" "$@"
