#!/usr/bin/env python3
"""
批量 addr2line 工具脚本
用于一次性解析大量 PC 地址
"""
import sys
import argparse
from pathlib import Path
from core.pc_resolver import PCResolver
from utils.config import Config


def main():
    parser = argparse.ArgumentParser(description='批量解析 PC 地址到源码行号')
    parser.add_argument('input_file', help='包含 PC 地址的输入文件（每行一个）')
    parser.add_argument('-o', '--output', help='输出文件路径')
    parser.add_argument('-v', '--vmlinux', default='./vmlinux',
                       help='vmlinux 文件路径')
    parser.add_argument('--cache', action='store_true',
                       help='使用缓存')
    
    args = parser.parse_args()
    
    # 读取输入文件
    if not Path(args.input_file).exists():
        print(f"错误：输入文件不存在：{args.input_file}")
        sys.exit(1)
    
    with open(args.input_file, 'r') as f:
        pcs = [line.strip() for line in f if line.strip()]
    
    if not pcs:
        print("错误：输入文件为空")
        sys.exit(1)
    
    print(f"读取到 {len(pcs)} 个 PC 地址")
    
    # 创建配置和解析器
    config = Config()
    config.vmlinux_path = args.vmlinux
    
    resolver = PCResolver(config)
    
    # 构建查找表
    cache_file = "./cache/pc_lookup_table.txt" if args.cache else None
    lookup_table = resolver.build_lookup_table(set(pcs), cache_file)
    
    print(f"解析完成，得到 {len(lookup_table)} 条记录")
    
    # 输出结果
    output_lines = []
    for pc in pcs:
        if pc in lookup_table:
            loc = lookup_table[pc]
            output_lines.append(f"{pc} -> {loc.function} ({loc.file}:{loc.line})")
        else:
            output_lines.append(f"{pc} -> (无法解析)")
    
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, 'w') as f:
            f.write('\n'.join(output_lines))
        print(f"结果已保存到：{args.output}")
    else:
        # 输出到 stdout
        for line in output_lines[:100]:  # 只显示前 100 条
            print(line)
        
        if len(output_lines) > 100:
            print(f"... 还有 {len(output_lines) - 100} 条记录")


if __name__ == "__main__":
    main()
