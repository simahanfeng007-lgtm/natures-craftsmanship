#!/bin/bash
# 天工造物 v2 - 临渊者 Linux 桌面壳启动脚本
# 用法: ./init.sh

set -e

JIAO_BEN_LUJING="$(cd "$(dirname "$0")" && pwd)"
cd "$JIAO_BEN_LUJING"

echo "[天工造物 v2.0] 临渊者桌面壳 Linux 启动"

# 检查 Node.js
if ! command -v node &>/dev/null; then
    echo "[错误] 未找到 Node.js。请先安装: curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs"
    exit 1
fi

echo "[信息] Node.js $(node --version) | npm $(npm --version)"

# 安装 Electron 依赖
if [ ! -d "node_modules" ]; then
    echo "[信息] 首次运行，正在安装 Electron 依赖..."
    npm install --registry=https://registry.npmmirror.com
    echo "[信息] 依赖安装完成"
fi

# 启动 Electron
echo "[信息] 正在启动桌面壳..."
npx electron . --no-sandbox
