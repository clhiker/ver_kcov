#!/usr/bin/env python3
"""
自动配置 Verifier 地址范围脚本
自动提取符号地址并更新配置文件
"""
import subprocess
import yaml
import re
import sys
from pathlib import Path


def extract_symbol_addresses(vmlinux_path: str) -> dict:
    """从 vmlinux 提取符号地址"""
    print(f"[*] 从 {vmlinux_path} 提取符号地址...")
    
    try:
        # 运行 nm 命令
        result = subprocess.run(
            ['nm', '-n', vmlinux_path],
            capture_output=True,
            text=True,
            check=True
        )
        
        lines = result.stdout.split('\n')
        
        # 查找 bpf_check 函数
        bpf_check_start = None
        bpf_check_end = None
        
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                addr = parts[0]
                sym_type = parts[1]
                sym_name = parts[2]
                
                # 查找 bpf_check 函数开始
                if sym_name == 'bpf_check' and sym_type in ['t', 'T']:
                    bpf_check_start = f"0x{addr}"
        
        # 查找 do_check 函数作为参考
        do_check_addr = None
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                addr = parts[0]
                sym_type = parts[1]
                sym_name = parts[2]
                
                if sym_name == 'do_check' and sym_type in ['t', 'T']:
                    do_check_addr = f"0x{addr}"
                    break
        
        # 如果没有找到 bpf_check，尝试使用 do_check
        if not bpf_check_start and do_check_addr:
            print(f"[!] 未找到 bpf_check，使用 do_check 作为参考")
            bpf_check_start = do_check_addr
        
        if bpf_check_start:
            # 估算结束地址（从起始地址偏移约 300KB）
            start_int = int(bpf_check_start, 16)
            estimated_end = start_int + 0x4a000  # 约 300KB
            bpf_check_end = f"0x{estimated_end:x}"
            
            return {
                'start': bpf_check_start,
                'end': bpf_check_end,
                'do_check': do_check_addr
            }
        
        return {}
        
    except subprocess.CalledProcessError as e:
        print(f"[!] 执行 nm 失败：{e.stderr}")
        return {}
    except FileNotFoundError:
        print(f"[!] 未找到 vmlinux 文件：{vmlinux_path}")
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
