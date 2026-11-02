#!/usr/bin/env bash
# 第一阶段安装脚本（在 Mac 上运行）：安装 iPhone-use、Python 依赖、可选的 libimobiledevice/pymobiledevice3。
# 不越狱、不装描述文件、不需要 Apple 开发者账号。
set -euo pipefail

IPHONE_USE_VERSION="${IPHONE_USE_VERSION:-v1.0.1}"
MODE="${1:-release}"   # release | source

echo "== 0. 系统要求：macOS 14+，蓝牙 LE，Apple Silicon/Intel =="
sw_vers
if ! command -v brew >/dev/null 2>&1; then
  echo "未安装 Homebrew：https://brew.sh" >&2; exit 1
fi

echo "== 1. 安装 USB 枚举工具（可选但推荐：UDID 交叉核对用） =="
brew list libimobiledevice >/dev/null 2>&1 || brew install libimobiledevice
python3 -m pip install --user -q pymobiledevice3 || true

echo "== 2. 安装 iPhone-use（${MODE}） =="
if [ "$MODE" = "source" ]; then
  if [ ! -d "$HOME/iPhone-use" ]; then
    git clone https://github.com/YueChen-C/iPhone-use.git "$HOME/iPhone-use"
  fi
  (cd "$HOME/iPhone-use" && swift build -c release)
  echo "源码构建完成：$HOME/iPhone-use/.build/release/iphone-use"
else
  TMP="$(mktemp -d)"
  ZIP="iPhoneUse-${IPHONE_USE_VERSION}-macOS-universal.zip"
  URL="https://github.com/YueChen-C/iPhone-use/releases/download/${IPHONE_USE_VERSION}/${ZIP}"
  echo "下载 ${URL}"
  curl -fL -o "$TMP/$ZIP" "$URL"
  curl -fL -o "$TMP/SHA256SUMS.txt" "https://github.com/YueChen-C/iPhone-use/releases/download/${IPHONE_USE_VERSION}/SHA256SUMS.txt" || true
  (cd "$TMP" && { [ -f SHA256SUMS.txt ] && shasum -a 256 -c SHA256SUMS.txt --ignore-missing || echo "跳过校验（无 SHA256SUMS）"; })
  (cd "$TMP" && unzip -q "$ZIP")
  rm -rf /Applications/iPhoneUse.app
  mv "$TMP"/iPhoneUse.app /Applications/
  echo "已安装 /Applications/iPhoneUse.app（ad-hoc 签名，首次打开需在 系统设置→隐私与安全性 允许）"
fi

echo "== 3. 安装群控工具 ifarm =="
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
pip install -q -e ".[dev]"
echo "完成。下一步：open -a iPhoneUse ；然后 .venv/bin/ifarm check"
