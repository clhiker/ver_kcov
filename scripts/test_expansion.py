#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from core.coverage_db import CoverageDatabase
from utils.config import DEFAULT_CONFIG_PATH, load_project_config

def main():
    config = load_project_config(str(DEFAULT_CONFIG_PATH))
    pc = "0xffffffff81dcd90f"
    with CoverageDatabase(config.db_path) as db:
        res = db.get_bb_expansion(pc)
        print(f"BB Expansion for {pc}: {res}")
        
if __name__ == "__main__":
    main()
