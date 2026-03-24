import sqlite3
import json
import hashlib
import os
from pathlib import Path
from collections import Counter

def get_traces_for_top_signatures_fast(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Get one sample testcase_id for each unique path_hash
    print("Sampling test cases for unique path hashes...")
    cursor.execute('''
        SELECT path_hash, MIN(id) as sample_tc_id, COUNT(*) as tc_count
        FROM test_cases
        GROUP BY path_hash
    ''')
    path_samples = cursor.fetchall()
    print(f"Found {len(path_samples)} unique path hashes.")

    # 2. Map each path_hash to its coverage_signature
    path_to_sig = {}
    for i, row in enumerate(path_samples):
        ph = row['path_hash']
        tc_id = row['sample_tc_id']
        
        if i % 100 == 0:
            print(f"Processing path {i}/{len(path_samples)}...")

        cursor.execute('''
            SELECT DISTINCT file_path, line_number
            FROM source_coverage
            WHERE testcase_id = ? AND file_path LIKE '%verifier.c'
            ORDER BY file_path, line_number
        ''', (tc_id,))
        lines = cursor.fetchall()
        
        if not lines:
            continue
            
        normalized_lines = [f"{Path(r['file_path']).name}:{r['line_number']}" for r in lines]
        sig = hashlib.sha256("\n".join(normalized_lines).encode()).hexdigest()[:16]
        path_to_sig[ph] = sig

    # 3. Aggregate test cases by signature
    sig_counts = Counter()
    sig_to_rep_path = {} # sig -> path_hash with most cases
    sig_path_counts = {} # sig -> {path_hash: count}
    
    for row in path_samples:
        ph = row['path_hash']
        count = row['tc_count']
        if ph in path_to_sig:
            sig = path_to_sig[ph]
            sig_counts[sig] += count
            if sig not in sig_path_counts:
                sig_path_counts[sig] = Counter()
            sig_path_counts[sig][ph] += count

    # 4. Sort and results
    sorted_sigs = sig_counts.most_common(10)
    
    results = []
    print("\nTop 10 Coverage Signatures (Aggregated):")
    for sig, total_count in sorted_sigs:
        rep_path_hash = sig_path_counts[sig].most_common(1)[0][0]
        
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
            'count': total_count,
            'rep_path_hash': rep_path_hash,
            'trace': trace_str
        })
        print(f"Sig {sig}: {total_count} cases")
        
    conn.close()
    return results

if __name__ == "__main__":
    db = "kcov_coverage.db"
    results = get_traces_for_top_signatures_fast(db)
    
    with open("optimized_signatures_report.txt", "w") as f:
        f.write("Top 10 Coverage Signatures and Representative Traces (Fast Mapping)\n")
        f.write("=" * 75 + "\n\n")
        for i, res in enumerate(results, 1):
            f.write(f"{i}. Signature: {res['signature']}\n")
            f.write(f"   Total Test Cases: {res['count']}\n")
            f.write(f"   Representative Path Hash: {res['rep_path_hash']}\n")
            f.write(f"   Representative Trace: {res['trace']}\n")
            f.write("-" * 75 + "\n")
    print("\nReport saved to optimized_signatures_report.txt")
