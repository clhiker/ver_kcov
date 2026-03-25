#!/usr/bin/env python3
import sys
import subprocess
import re
import json
import yaml
from pathlib import Path

def load_config(config_path):
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_disassembly(vmlinux_path, start_addr, end_addr):
    print(f"[*] 正在对 {vmlinux_path} 范围 {start_addr}-{end_addr} 执行反汇编...")
    cmd = [
        'objdump', '-d', vmlinux_path,
        f'--start-address={start_addr}',
        f'--stop-address={end_addr}'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout

def parse_bb_expansion(disasm_text):
    print("[*] 正在解析基本块边界...")
    
    # 匹配地址: 汇编指令
    # 示例: ffffffff81dcd90a:       e8 71 94 e6 ff          call   ffffffff81c36d80 <__sanitizer_cov_trace_pc>
    line_re = re.compile(r'^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2}\s+)+(.+)$')
    
    trace_pc_re = re.compile(r'call.*sanitizer_cov_trace_pc')
    branch_re = re.compile(r'^(jmp|je|jne|jg|jl|ja|jb|jge|jle|jae|jbe|call|ret|loop|syscall)')
    
    expansion_map = {} # trace_pc -> list of instruction addresses
    
    current_bb_trace_pc = None
    current_bb_instrs = []
    
    lines = disasm_text.splitlines()
    for i, line in enumerate(lines):
        match = line_re.match(line)
        if not match:
            continue
            
        addr_hex = match.group(1)
        addr = int(addr_hex, 16)
        instr = match.group(2).strip()
        
        # 如果是插桩调用
        if trace_pc_re.search(instr):
            # 记录之前的（如果有）
            if current_bb_trace_pc and current_bb_instrs:
                expansion_map[current_bb_trace_pc] = current_bb_instrs
            
            # 找到返回地址（即 trace 记录的 PC）
            next_instr_addr = None
            if i + 1 < len(lines):
                next_match = line_re.match(lines[i+1])
                if next_match:
                    next_instr_addr = f"0x{next_match.group(1)}"
            
            if next_instr_addr:
                current_bb_trace_pc = next_instr_addr
                current_bb_instrs = [next_instr_addr]
            else:
                current_bb_trace_pc = None
                current_bb_instrs = []
            continue
            
        if current_bb_trace_pc:
            current_bb_instrs.append(f"0x{addr_hex}")
                
    # 最后一个
    if current_bb_trace_pc and current_bb_instrs:
        expansion_map[current_bb_trace_pc] = current_bb_instrs
        
    return expansion_map

def resolve_lines(vmlinux_path, all_addrs):
    if not all_addrs:
        return {}
        
    print(f"[*] 正在批量解析 {len(all_addrs)} 个地址的源码行...")
    cmd = ['llvm-symbolizer', '-e', vmlinux_path, '--functions', '--inlining', '--output-style=JSON']
    
    # 分批处理以避免命令行过长
    BATCH_SIZE = 5000
    addr_list = list(all_addrs)
    results = {}
    
    for i in range(0, len(addr_list), BATCH_SIZE):
        batch = addr_list[i:i+BATCH_SIZE]
        input_text = "\n".join(batch)
        
        res = subprocess.run(cmd, input=input_text, capture_output=True, text=True, check=True)
        for line in res.stdout.splitlines():
            if not line.strip(): continue
            record = json.loads(line)
            pc = record['Address']
            symbols = record.get('Symbol', [])
            
            lines = []
            for sym in symbols:
                if sym.get('FileName') and sym.get('Line'):
                    # 只保留 verifier.c 相关（可选，但这里我们收集所有以便后续过滤）
                    lines.append({
                        'file': sym['FileName'],
                        'line': sym['Line']
                    })
            results[pc] = lines
            
    return results

def main():
    config_path = "config/kcov_config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
        
    config = load_config(config_path)
    # 尝试解析 vmlinux 路径
    vmlinux = config['vmlinux_path']
    if not Path(vmlinux).exists():
        # 尝试在当前目录找
        alt_path = Path(vmlinux).name
        if Path(alt_path).exists():
            vmlinux = alt_path
        else:
            # 尝试相对于 config 文件目录找
            alt_path = Path(config_path).parent / config['vmlinux_path']
            if alt_path.exists():
                vmlinux = str(alt_path)
    
    if not Path(vmlinux).exists():
        print(f"[!] 无法找到 vmlinux 文件: {config['vmlinux_path']}")
        sys.exit(1)
        
    start = config['verifier_start_addr']
    end = config['verifier_end_addr']
    
    # 1. 获取汇编
    disasm = get_disassembly(vmlinux, start, end)
    
    # 2. 解析 BB 展开
    expansion_map = parse_bb_expansion(disasm)
    print(f"[*] 发现 {len(expansion_map)} 个基本块采样点")
    
    # 3. 收集所有需要解析的地址
    all_addrs_to_resolve = set()
    for addrs in expansion_map.values():
        all_addrs_to_resolve.update(addrs)
        
    # 4. 解析
    addr_to_lines = resolve_lines(vmlinux, all_addrs_to_resolve)
    
    # 5. 组装最终映射: trace_pc -> list of unique line numbers (for verifier.c)
    suffix = "verifier.c" # 或者从 config 获取
    final_map = {}
    for trace_pc, bb_addrs in expansion_map.items():
        unique_lines = set()
        for addr in bb_addrs:
            lines = addr_to_lines.get(addr, [])
            for l in lines:
                if l['file'].endswith(suffix):
                    unique_lines.add(l['line'])
        if unique_lines:
            final_map[trace_pc] = sorted(list(unique_lines))
            
    # 6. 输出结果（暂时保存到 JSON，后续入库）
    output_path = "cache/bb_expansion.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(final_map, f, indent=2)
        
    print(f"[*] 已生成基本块展开表，共 {len(final_map)} 条映射，保存至 {output_path}")

if __name__ == "__main__":
    main()
