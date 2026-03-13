#!/bin/bash
#
# 快速配置脚本 - 一键完成所有准备工作
#

set -e

echo "=============================================="
echo "Verifier 覆盖率采集框架 - 快速配置"
echo "=============================================="
echo ""

# 检查 vmlinux 文件
VMLINUX="${1:-./vmlinux}"
if [ ! -f "$VMLINUX" ]; then
    echo "[!] 错误：找不到 vmlinux 文件：$VMLINUX"
    echo ""
    echo "使用方法:"
    echo "  $0 [vmlinux_path]"
    echo ""
    exit 1
fi

echo "[*] 检查 vmlinux 文件：$VMLINUX"
if file "$VMLINUX" | grep -q "with debug_info"; then
    echo "✓ vmlinux 包含调试信息"
else
    echo "[!] 警告：vmlinux 可能不包含调试信息"
fi
echo ""

# 运行自动配置
echo "[*] 运行自动配置..."
python3 scripts/auto_config.py "$VMLINUX" ./config/kcov_config.yaml
echo ""

# 检查 kcov_runner
echo "[*] 检查 kcov_runner..."
if [ -f "./kcov_runner" ]; then
    echo "✓ kcov_runner 已存在"
else
    echo "[*] 编译 kcov_runner..."
    make
fi
echo ""

# 检查测试用例
echo "[*] 检查测试用例..."
TESTCASE_COUNT=$(find ./testcases -name "*.o" 2>/dev/null | wc -l)
if [ "$TESTCASE_COUNT" -gt 0 ]; then
    echo "✓ 找到 $TESTCASE_COUNT 个测试用例"
else
    echo "[!] 警告：testcases 目录没有找到 .o 文件"
fi
echo ""

echo "=============================================="
echo "✓ 快速配置完成！"
echo "=============================================="
echo ""
echo "下一步:"
echo "  1. 检查配置文件：cat config/kcov_config.yaml"
echo "  2. 运行覆盖率采集：sudo python3 main.py run"
echo ""
