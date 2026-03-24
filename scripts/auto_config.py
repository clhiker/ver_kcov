#!/usr/bin/env python3
"""
自动配置 Verifier 地址范围脚本
自动提取符号地址并更新配置文件
"""
import subprocess
import yaml
import json
import sys
from pathlib import Path


def _load_text_symbols(vmlinux_path: str) -> list:
    """加载 vmlinux 中的文本符号地址和大小。"""
    result = subprocess.run(
        ['nm', '-S', '-n', vmlinux_path],
        capture_output=True,
        text=True,
        check=True
    )

    symbols = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue

        addr = parts[0]
        if len(parts) >= 4 and parts[2] in ['t', 'T']:
            size = parts[1]
            sym_type = parts[2]
            sym_name = parts[3]
        elif len(parts) >= 3 and parts[1] in ['t', 'T']:
            size = "0"
            sym_type = parts[1]
            sym_name = parts[2]
        else:
            continue

        symbols.append({
            'addr': int(addr, 16),
            'addr_hex': f"0x{addr}",
            'size': int(size, 16),
            'name': sym_name,
        })

    return symbols


def _symbolize_addresses(vmlinux_path: str, addresses: list[str]) -> dict[str, str]:
    """批量解析地址对应的源码文件。"""
    if not addresses:
        return {}

    result = subprocess.run(
        ['llvm-symbolizer', '-e', vmlinux_path, '--output-style=JSON'],
        input="\n".join(addresses),
        capture_output=True,
        text=True,
        check=True
    )

    mapping = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        symbols = record.get('Symbol') or []
        if not symbols:
            continue
        file_name = symbols[0].get('FileName', '')
        mapping[record['Address']] = file_name

    return mapping


def extract_symbol_addresses(vmlinux_path: str) -> dict:
    """从 vmlinux 提取 verifier.c 的实际地址范围。"""
    print(f"[*] 从 {vmlinux_path} 提取符号地址...")
    
    try:
        symbols = _load_text_symbols(vmlinux_path)
        bpf_check = next((s for s in symbols if s['name'] == 'bpf_check'), None)
        if not bpf_check:
            return {}

        files_by_addr = _symbolize_addresses(
            vmlinux_path,
            [s['addr_hex'] for s in symbols]
        )

        verifier_symbols = [
            s for s in symbols
            if files_by_addr.get(s['addr_hex'], '').endswith('/kernel/bpf/verifier.c')
        ]

        if not verifier_symbols:
            return {}

        clusters = []
        current_cluster = [verifier_symbols[0]]
        for symbol in verifier_symbols[1:]:
            if symbol['addr'] - current_cluster[-1]['addr'] > 0x40000:
                clusters.append(current_cluster)
                current_cluster = [symbol]
            else:
                current_cluster.append(symbol)
        clusters.append(current_cluster)

        verifier_cluster = next(
            (cluster for cluster in clusters if any(s['name'] == 'bpf_check' for s in cluster)),
            None
        )
        if not verifier_cluster:
            return {}

        start_addr = verifier_cluster[0]['addr']
        end_addr = verifier_cluster[-1]['addr'] + verifier_cluster[-1].get('size', 0)

        do_check_addr = None
        do_check = next((s for s in verifier_cluster if s['name'] == 'do_check'), None)
        if do_check:
            do_check_addr = do_check['addr_hex']

        return {
            'start': f"0x{start_addr:x}",
            'end': f"0x{end_addr:x}",
            'do_check': do_check_addr
        }
        
    except subprocess.CalledProcessError as e:
        print(f"[!] 执行符号提取失败：{e.stderr}")
        return {}
    except FileNotFoundError:
        print(f"[!] 未找到 vmlinux 或 llvm-symbolizer：{vmlinux_path}")
        return {}


def update_config(config_path: str, addresses: dict):
    """更新配置文件"""
    if not addresses:
        print("[!] 没有地址信息可更新")
        return False
    
    config_file = Path(config_path)
    if not config_file.exists():
        print(f"[!] 配置文件不存在：{config_file}")
        return False
    
    print(f"[*] 更新配置文件：{config_file}")
    
    # 读取现有配置
    with open(config_file, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 更新地址
    if 'start' in addresses:
        config['verifier_start_addr'] = addresses['start']
        print(f"✓ verifier_start_addr: {addresses['start']}")
    
    if 'end' in addresses:
        config['verifier_end_addr'] = addresses['end']
        print(f"✓ verifier_end_addr: {addresses['end']}")
    
    if 'do_check' in addresses:
        print(f"  do_check 地址：{addresses['do_check']}")
    
    # 保存配置
    with open(config_file, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
    
    print(f"✓ 配置文件已更新")
    return True


def main():
    print("="*60)
    print("自动配置 Verifier 地址范围")
    print("="*60)
    
    # 参数
    vmlinux_path = sys.argv[1] if len(sys.argv) > 1 else "./vmlinux"
    config_path = sys.argv[2] if len(sys.argv) > 2 else "./config/kcov_config.yaml"
    
    # 检查 vmlinux 文件
    if not Path(vmlinux_path).exists():
        print(f"[!] 错误：找不到 vmlinux 文件：{vmlinux_path}")
        print("\n使用方法:")
        print(f"  {sys.argv[0]} [vmlinux_path] [config_path]")
        print(f"示例:")
        print(f"  {sys.argv[0]} ./vmlinux ./config/kcov_config.yaml")
        sys.exit(1)
    
    # 提取符号地址
    addresses = extract_symbol_addresses(vmlinux_path)
    
    if not addresses:
        print("[!] 未能提取到符号地址")
        print("\n请手动执行以下步骤:")
        print("1. 运行：nm -n vmlinux | grep bpf_check")
        print("2. 找到 bpf_check 的地址")
        print("3. 手动编辑 config/kcov_config.yaml")
        sys.exit(1)
    
    print(f"\n[*] 提取到的地址信息:")
    print(f"  bpf_check 起始：{addresses.get('start', 'N/A')}")
    print(f"  bpf_check 结束：{addresses.get('end', 'N/A')}")
    if addresses.get('do_check'):
        print(f"  do_check: {addresses['do_check']}")
    
    # 更新配置文件
    print()
    success = update_config(config_path, addresses)
    
    if success:
        print("\n" + "="*60)
        print("✓ 自动配置完成！")
        print("="*60)
        print(f"\n下一步:")
        print(f"  1. 检查配置文件：{config_path}")
        print(f"  2. 运行：sudo python3 main.py run")
    else:
        print("\n[!] 配置更新失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
