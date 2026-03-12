import os
import subprocess
import hashlib
import json
from pathlib import Path

# --- 配置区 ---
VMLINUX_PATH = "./vmlinux"
KCOV_RUNNER = "./kcov_runner"
TESTCASE_DIR = "./testcases"
RESULT_DIR = "./path_results"
# 填入你通过 nm 获取的地址范围
VERIFIER_START = 0xffffffff811a4500 
VERIFIER_END = 0xffffffff811b2000
# -------------

def get_fingerprint(pcs):
    """过滤地址、连续去重并生成哈希指纹"""
    filtered = []
    last_pc = None
    for pc in pcs:
        val = int(pc, 16)
        if VERIFIER_START <= val <= VERIFIER_END:
            if pc != last_pc: # 连续去重（Fold）
                filtered.append(pc)
                last_pc = pc
    
    path_str = ",".join(filtered)
    path_hash = hashlib.sha256(path_str.encode()).hexdigest()[:16] # 取前16位作为 ID
    return path_hash, filtered

def main():
    Path(RESULT_DIR).mkdir(exist_ok=True)
    mapping = {} # {test_case: path_id}
    unique_paths = {} # {path_id: pc_list}

    testcases = list(Path(TESTCASE_DIR).glob("*.o"))
    print(f"[*] 发现 {len(testcases)} 个测试用例，开始分析...")

    for ts in testcases:
        try:
            # 1. 运行采集器 (假设你的 C 程序接收文件名作为参数)
            subprocess.run([KCOV_RUNNER, str(ts)], capture_output=True, check=True)
            
            # 2. 读取 PC 序列
            with open("verifier_pcs.txt", "r") as f:
                raw_pcs = [line.strip() for line in f.readlines()]
            
            # 3. 生成指纹
            path_id, clean_pcs = get_fingerprint(raw_pcs)
            
            # 4. 记录对应关系
            mapping[ts.name] = path_id
            if path_id not in unique_paths:
                unique_paths[path_id] = clean_pcs
                # 保存该唯一路径的 PC 序列，方便后续跑 addr2line
                with open(f"{RESULT_DIR}/path_{path_id}.pcs", "w") as f:
                    f.write("\n".join(clean_pcs))
            
            print(f"[+] {ts.name} -> Path ID: {path_id} ({len(clean_pcs)} PCs)")

        except Exception as e:
            print(f"[!] 处理 {ts.name} 失败: {e}")

    # 5. 保存结果索引
    with open("mapping_report.json", "w") as f:
        json.dump({"mapping": mapping, "unique_paths_count": len(unique_paths)}, f, indent=4)
    
    print(f"\n[*] 分析完成！")
    print(f"[*] 唯一路径数量: {len(unique_paths)}")
    print(f"[*] 索引文件已保存至 mapping_report.json")

if __name__ == "__main__":
    main()