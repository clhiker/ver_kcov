#!/bin/bash
# 提取 Verifier 相关函数的地址范围
# 使用方法：./extract_symbols.sh

VMLINUX="${1:-./vmlinux}"

if [ ! -f "$VMLINUX" ]; then
    echo "错误：找不到 vmlinux 文件：$VMLINUX"
    exit 1
fi

echo "从 $VMLINUX 提取符号地址..."
echo "======================================"

# 提取 bpf_check 相关函数
echo -e "\n=== bpf_check 相关函数 ==="
nm -n "$VMLINUX" | grep -E " [tT] bpf_check" | head -20

# 提取 check_ 开头的函数
echo -e "\n=== check_* 函数（前 20 个）==="
nm -n "$VMLINUX" | grep -E " [tT] check_" | head -20

# 提取 verifier.c 相关函数
echo -e "\n=== verifier.c 相关函数 ==="
nm -n "$VMLINUX" | grep -E " [tT] (do_check|check_bpf_func|convert_bpf_function)" | head -10

# 获取地址范围建议
echo -e "\n======================================"
echo "建议的 address range 配置:"
echo "从上面找到 bpf_check 的起始地址和结束地址"
echo "示例："
echo "  verifier_start_addr: 0xXXXXXXXX"
echo "  verifier_end_addr: 0xXXXXXXXX"

# 生成配置片段
echo -e "\n======================================"
echo "可直接复制到 kcov_config.yaml 的配置:"
echo "---"
START_ADDR=$(nm -n "$VMLINUX" | grep -E " [tT] bpf_check$" | awk '{print $1}' | head -1)
if [ -n "$START_ADDR" ]; then
    echo "verifier_start_addr: \"0x$START_ADDR\""
    echo "# 结束地址需要根据函数大小估算，或查看下一个符号的地址"
    echo "# verifier_end_addr: \"0x...\""
else
    echo "# 未找到 bpf_check 符号，请手动指定"
fi
echo "---"
