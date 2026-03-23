import os
import sqlite3
import subprocess
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.vm_utils import detect_guest_workdir, run_guest_command, vm_config_from_project_config
from utils.config import load_project_config

CONFIG = load_project_config()
DB_PATH = CONFIG.db_path

def get_dense_and_sparse_paths():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT path_hash, COUNT(id) as tc_count, GROUP_CONCAT(name) as tcs
        FROM test_cases
        WHERE path_hash != '' AND path_hash IS NOT NULL
        GROUP BY path_hash
        ORDER BY tc_count DESC
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return [], []
        
    dense = [r for r in rows if r['tc_count'] >= 2]
    sparse = [r for r in rows if r['tc_count'] == 1]
    
    return dense, sparse

def run_enrichment():
    dense, sparse = get_dense_and_sparse_paths()
    print(f"[*] Found {len(dense)} Dense Paths and {len(sparse)} Sparse Paths.")
    if not dense:
        print("[!] No dense paths to mutate from.")
        return
        
    # Get a starting seed from the top dense path
    top_dense = dense[0]
    seed_filename = top_dense['tcs'].split(',')[0] # e.g. re_3.o or 0.o
    
    print(f"[*] Selecting seed {seed_filename} from Dense Path {top_dense['path_hash']} (count: {top_dense['tc_count']})")
    
    # Map .o to .c
    # if re_3.o -> 3.c. if 0.o -> 0.c
    base_idx = seed_filename.replace("re_", "").replace(".o", "")
    seed_c = f"testcases/code/{base_idx}.c"
    if not os.path.exists(seed_c):
        seed_c = f"mid-cases/code/{base_idx}.c"
    
    if not os.path.exists(seed_c):
        print(f"[!] Seed source {seed_c} not found.")
        return
        
    out_dir = "mutated-cases"
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
        
    print(f"[*] Mutating {seed_c}...")
    subprocess.run(["python3", "scripts/mutator.py", "-i", seed_c, "-n", "30", "-o", out_dir], check=True)
    
    valid_mutants = list(Path(out_dir).glob("*.o"))
    print(f"[*] Generated {len(valid_mutants)} valid compiled mutants.")
    
    if not valid_mutants:
        return
        
    # Run the coverage pipeline inside the VM via SSH
    print("[*] Running coverage pipeline on mutants...")
    vm_config = vm_config_from_project_config(CONFIG)
    guest_workdir = detect_guest_workdir(vm_config)
    guest_res = run_guest_command(
        vm_config,
        f"python3 main.py run -t {out_dir} --path-type full -p",
        workdir=guest_workdir,
    )
    if guest_res.stdout.strip():
        print(guest_res.stdout.strip())
    if guest_res.stderr.strip():
        print(guest_res.stderr.strip())
    
    # Check what happened
    print("[*] Re-evaluating paths...")
    new_dense, new_sparse = get_dense_and_sparse_paths()
    
    sparse_hashes = set(r['path_hash'] for r in sparse)
    new_sparse_hashes = set(r['path_hash'] for r in new_sparse)
    
    # Any new completely unique sparse paths?
    discovered = new_sparse_hashes - sparse_hashes
    if discovered:
        print(f"[+] SUCCESS! Discovered {len(discovered)} completely NEW execution paths through mutation!")
        for h in discovered:
            print(f"    - New Path Hash: {h}")
    else:
        print("[-] Mutants fell into existing dense paths or failed verifier checks in the same way.")
        

if __name__ == "__main__":
    run_enrichment()
