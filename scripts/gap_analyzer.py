import sqlite3
import re
import os

DB_PATH = "kcov_coverage.db"
VERIFIER_SRC = "verifier.c"

def get_covered_lines():
    if not os.path.exists(DB_PATH):
        return set()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # vmlinux paths look like "kernel/bpf/verifier.c"
    cursor.execute('''
        SELECT DISTINCT line_number 
        FROM source_coverage 
        WHERE file_path LIKE '%verifier.c'
    ''')
    lines = {row[0] for row in cursor.fetchall()}
    conn.close()
    return lines

def analyze_gaps():
    covered = get_covered_lines()
    print(f"[*] Total covered lines in verifier.c globally: {len(covered)}")
    
    if not os.path.exists(VERIFIER_SRC):
        print(f"[!] {VERIFIER_SRC} not found locally.")
        return
        
    with open(VERIFIER_SRC, 'r', encoding='utf-8') as f:
        src_lines = f.readlines()
        
    gaps = []
    
    for i, line in enumerate(src_lines):
        line_num = i + 1
        # If this line is an 'if' statement and it WAS hit
        if line_num in covered and re.match(r'^\s*if\s*\(', line):
            # Check the next few lines (the inner blocks)
            # A simplistic heuristic: check lines i+1 to i+3
            inner_hit = False
            for offset in range(1, 4):
                if line_num + offset in covered:
                    inner_hit = True
                    break
                    
            if not inner_hit:
                # This 'if' was evaluated, but the immediate following statements were NEVER hit!
                # This means we found a border gap!
                context = "".join(src_lines[i:i+4]).strip()
                gaps.append((line_num, context))
                
    print(f"[*] Found {len(gaps)} Borderline Branch Gaps (if-statement hit, but following block unreached):\n")
    
    # Print the top 10 most interesting gaps
    for line_num, ctx in gaps[:10]:
        print(f"--- Line {line_num} Gap ---")
        print(ctx)
        print("-" * 30)
        
if __name__ == "__main__":
    analyze_gaps()
