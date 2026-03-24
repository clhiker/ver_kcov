import sqlite3
import json
import hashlib
import os
from pathlib import Path
from collections import Counter

def get_top_10_signatures(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Get all test cases
    cursor.execute('SELECT id, name, path_hash FROM test_cases')
    test_cases = cursor.fetchall()
    
    # 2. Group by coverage signature
    # signature -> {'testcases': [names], 'path_hashes': {hash: count}}
    groups = {}
    
    print(f"Processing {len(test_cases)} test cases...")
    
    for i, tc in enumerate(test_cases):
        tc_id = tc['id']
        path_hash = tc['path_hash']
        
        # Get unique lines for this test case
        cursor.execute('''
            SELECT DISTINCT file_path, line_number
            FROM source_coverage
            WHERE testcase_id = ? AND file_path LIKE '%verifier.c'
            ORDER BY file_path, line_number
        ''', (tc_id,))
        lines = cursor.fetchall()
        
        if not lines:
            continue
            
        normalized_lines = [f"{Path(row['file_path']).name}:{row['line_number']}" for row in lines]
        normalized_lines.sort()
        signature = hashlib.sha256("\n".join(normalized_lines).encode()).hexdigest()[:16]
        
        if signature not in groups:
            groups[signature] = {
                'count': 0,
                'path_hashes': Counter(),
                'lines_count': len(normalized_lines)
            }
        
        groups[signature]['count'] += 1
        groups[signature]['path_hashes'][path_hash] += 1
        
    # 3. Sort by count
    sorted_groups = sorted(groups.items(), key=lambda x: x[1]['count'], reverse=True)
    
    results = []
    print("\nTop 10 Coverage Signatures:")
    print(f"{'Signature':<20} | {'Test Cases':<10} | {'Lines':<5}")
    print("-" * 45)
    
    for sig, info in sorted_groups[:10]:
        print(f"{sig:<20} | {info['count']:<10} | {info['lines_count']:<5}")
        
        # Pick most frequent path_hash as representative
        rep_path_hash = info['path_hashes'].most_common(1)[0][0]
        
        # Get trace for representative
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
            'count': info['count'],
            'lines_count': info['lines_count'],
            'rep_path_hash': rep_path_hash,
            'trace': trace_str
        })
        
    conn.close()
    return results

if __name__ == "__main__":
    db = "kcov_coverage.db"
    results = get_top_10_signatures(db)
    
    with open("top_10_signatures_report.txt", "w") as f:
        f.write("Top 10 Coverage Signatures and Representative Traces\n")
        f.write("=" * 60 + "\n\n")
        f.write("注：覆盖签名（Coverage Signature）基于覆盖的代码行集合进行分组，\n")
        f.write("一个签名可能包含多种不同的执行路径（Path Hash）。\n\n")
        for i, res in enumerate(results, 1):
            f.write(f"{i}. Signature: {res['signature']}\n")
            f.write(f"   Test Cases: {res['count']}\n")
            f.write(f"   Unique Lines: {res['lines_count']}\n")
            f.write(f"   Rep Path Hash: {res['rep_path_hash']}\n")
            f.write(f"   Representative Trace: {res['trace']}\n")
            f.write("-" * 60 + "\n")
    print("\nReport saved to top_10_signatures_report.txt")
