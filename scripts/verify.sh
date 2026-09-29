#!/bin/sh
# verify 服务入口：构建检查 → 代码测试 → 接口/HTTP 冒烟；任一步失败即以非零退出。
set -eu

echo "== [verify] 1/3 构建检查：字节码编译全部模块 =="
python3 -m compileall -q app scripts

echo "== [verify] 2/3 代码测试：弱互模拟审计与校验单元测试 =="
python3 -m unittest discover -s tests -v

echo "== [verify] 3/3 接口 / HTTP 冒烟：${BASE_URL} =="
python3 scripts/http_smoke.py

echo "== [verify] 全部检验通过 =="
