import sqlite3
import json
import hashlib
import os
from pathlib import Path
from collections import Counter

def get_traces_for_target_signatures(db_path, target_sigs):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Map all testcases to their signatures (Optimized)
    print("Building testcase -> signature mapping...")
    cursor.execute('''
        SELECT testcase_id, GROUP_CONCAT(file_path || ':' || line_number) as lines
        FROM (
            SELECT DISTINCT testcase_id, file_path, line_number
            FROM source_coverage
            WHERE file_path LIKE '%verifier.c'
            ORDER BY testcase_id, file_path, line_number
        )
        GROUP BY testcase_id
    ''')
    
    tc_sigs = {}
    for row in cursor.fetchall():
        lines_str = row['lines'].replace(',', '\n') # GROUP_CONCAT uses comma by default
        sig = hashlib.sha256(lines_str.encode()).hexdigest()[:16]
        tc_sigs[row['testcase_id']] = sig

    # 2. Group path_hashes by signature
    sig_to_paths = {} # sig -> Counter(path_hash)
    
    cursor.execute('SELECT id, path_hash FROM test_cases')
    for row in cursor.fetchall():
        tc_id = row['id']
        path_hash = row['path_hash']
        if tc_id in tc_sigs:
            sig = tc_sigs[tc_id]
            if sig not in sig_to_paths:
                sig_to_paths[sig] = Counter()
            sig_to_paths[sig][path_hash] += 1

    # 3. Process targets
    results = []
    print("\nProcessing target signatures:")
    for sig in target_sigs:
        if sig not in sig_to_paths:
            print(f"Signature {sig} not found in database.")
            continue
            
        count = sum(sig_to_paths[sig].values())
        rep_path_hash = sig_to_paths[sig].most_common(1)[0][0]
        
        # Get trace
        cursor.execute('SELECT sequence_json FROM execution_path_sequences WHERE path_hash = ?', (rep_path_hash,))
        res = cursor.fetchone()
        
        trace_str = "No trace found"
        if res and res['sequence_json']:
            seq = json.loads(res['sequence_json'])
            trace_parts = []
            for file_path, lines_list in seq.items():
                trace_parts.append(f"{Path(file_path).name}: {' -> '.join(map(str, lines_list))}")
            trace_str = " | ".join(trace_parts)
            
        results.append({
            'signature': sig,
            'count': count,
            'rep_path_hash': rep_path_hash,
            'trace': trace_str
        })
        print(f"Found {sig}: {count} cases, representative hash {rep_path_hash[:16]}")
        
    conn.close()
    return results

if __name__ == "__main__":
    db = "kcov_coverage.db"
    targets = [
        "af487f6219b4eb14", "d6053da842b0cf73", "1cb125e776203c9e",
        "ccd9115e82706c06", "7d40ebe97e0f5d29", "b085529b9b8ff814",
        "4483e971880ac9f1", "8a4d41d83313667d"
    ]
    results = get_traces_for_target_signatures(db, targets)
    
    with open("user_requested_signatures_report.txt", "w") as f:
        f.write("User Requested Coverage Signatures and Representative Traces\n")
        f.write("=" * 65 + "\n\n")
        for i, res in enumerate(results, 1):
            f.write(f"{i}. Signature: {res['signature']}\n")
            f.write(f"   Test Cases (Total in Group): {res['count']}\n")
            f.write(f"   Representative Path Hash: {res['rep_path_hash']}\n")
            f.write(f"   Representative Trace: {res['trace']}\n")
            f.write("-" * 65 + "\n")
    print("\nReport saved to user_requested_signatures_report.txt")
