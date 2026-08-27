#!/bin/bash
# 成绩登记 —— 打包成免安装的 macOS 程序
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  成绩登记 —— 打包成免安装的 macOS 程序"
echo "============================================"
echo

PY=python3
command -v $PY >/dev/null 2>&1 || { echo "[错误] 没有找到 python3，请先安装 Python 3.10 以上版本"; exit 1; }

if [ ! -d ".venv" ]; then
    echo "[1/5] 创建虚拟环境…"
    $PY -m venv .venv
else
    echo "[1/5] 虚拟环境已存在，跳过"
fi

echo "[2/5] 安装依赖（首次约需几分钟）…"
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q
.venv/bin/python -m pip install pyinstaller -q

echo "[3/5] 检查语音模型…"
if [ ! -f "models/sense-voice/model.int8.onnx" ]; then
    echo "      模型不在本地，开始下载（约 228MB，只需一次）…"
    .venv/bin/python download_model.py
else
    echo "      模型已就位"
fi

echo "[4/5] 打包中（约 2-5 分钟）…"
rm -rf build dist
.venv/bin/pyinstaller GradeRegister.spec --noconfirm >/dev/null

echo "[5/5] 制作 DMG 安装包…"
APP="dist/成绩登记.app"
DMG="dist/成绩登记.dmg"
rm -f "$DMG"
STAGING=$(mktemp -d)
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "成绩登记" -srcfolder "$STAGING" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGING"

echo
echo "============================================"
echo "  打包完成"
echo "============================================"
echo "  程序:     $APP"
echo "  安装包:   $DMG  ($(du -h "$DMG" | cut -f1))"
echo
echo "  换台 Mac 首次打开时，系统会拦一下（因为没有花钱买苹果的开发者签名）："
echo "  右键点图标 → 打开 → 再点「打开」，之后就跟正常软件一样。"
echo
