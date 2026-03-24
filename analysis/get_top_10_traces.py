import sqlite3
import json
import os
from pathlib import Path

def get_top_10_traces(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Get top 10 path_hash by test case count
    cursor.execute('''
        SELECT path_hash, COUNT(*) as tc_count
        FROM test_cases
        GROUP BY path_hash
        ORDER BY tc_count DESC
        LIMIT 10
    ''')
    top_paths = cursor.fetchall()

    print(f"{'Path Hash':<20} | {'Test Cases':<10}")
    print("-" * 33)
    
    results = []
    for row in top_paths:
        path_hash = row['path_hash']
        tc_count = row['tc_count']
        print(f"{path_hash[:16]:<20} | {tc_count:<10}")
        
        # 2. Get trace
        cursor.execute('SELECT sequence_json FROM execution_path_sequences WHERE path_hash = ?', (path_hash,))
        res = cursor.fetchone()
        
        trace_str = "No trace found"
        if res and res['sequence_json']:
            seq = json.loads(res['sequence_json'])
            # Format: 'verifier.c: 123 -> 456'
            trace_parts = []
            for file_path, lines in seq.items():
                file_name = Path(file_path).name
                trace_parts.append(f"{file_name}: {' -> '.join(map(str, lines))}")
            trace_str = " | ".join(trace_parts)
        
        results.append({
            'path_hash': path_hash,
            'tc_count': tc_count,
            'trace': trace_str
        })

    conn.close()
    return results

if __name__ == "__main__":
    db = "kcov_coverage.db"
    results = get_top_10_traces(db)
    
    with open("top_10_traces_report.txt", "w") as f:
        f.write("Top 10 Execution Paths and Traces\n")
        f.write("=" * 40 + "\n\n")
        for i, res in enumerate(results, 1):
            f.write(f"{i}. Path Hash: {res['path_hash']}\n")
            f.write(f"   Test Cases: {res['tc_count']}\n")
            f.write(f"   Trace: {res['trace']}\n")
            f.write("-" * 40 + "\n")
    print("\nReport saved to top_10_traces_report.txt")
