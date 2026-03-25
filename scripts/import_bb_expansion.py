#!/usr/bin/env python3
import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.append(str(Path(__file__).parent.parent))

from core.coverage_db import CoverageDatabase
from utils.config import DEFAULT_CONFIG_PATH, load_project_config

def main():
    config = load_project_config(str(DEFAULT_CONFIG_PATH))
    json_path = Path("cache/bb_expansion.json")
    
    if not json_path.exists():
        print(f"[!] 找不到展开表文件: {json_path}")
        return
        
    with open(json_path, 'r') as f:
        expansion_map = json.load(f)
        
    print(f"[*] 正在导入 {len(expansion_map)} 条基本块展开记录到 {config.db_path}...")
    
    with CoverageDatabase(config.db_path) as db:
        db.save_bb_expansions(expansion_map)
        
    print("[*] 导入完成！")

if __name__ == "__main__":
    main()
