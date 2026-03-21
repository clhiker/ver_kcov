import sqlite3
import json
import itertools
import sys
from collections import Counter

def jaccard_similarity(list1, list2):
    """
    Calculate the Jaccard similarity between two lists.
    Since execution/stable paths are ordered, we can also compute sequence alignment, 
    but Jaccard on the set of nodes is a good baseline for node-coverage similarity.
    """
    s1 = set(list1)
    s2 = set(list2)
    if not s1 or not s2:
        return 0.0
    return len(s1.intersection(s2)) / len(s1.union(s2))

def sequence_matcher_similarity(list1, list2):
    """
    Calculate similarity based on sequence (order matters), using difflib.
    """
    import difflib
    sm = difflib.SequenceMatcher(None, list1, list2)
    return sm.ratio()

def analyze_paths(db_path, path_type='stable'):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    hash_column = 'stable_path_hash' if path_type == 'stable' else 'path_hash'
    seq_table = 'stable_path_sequences' if path_type == 'stable' else 'execution_path_sequences'

    # 1. Count testcases per path
    cursor.execute(f'''
        SELECT {hash_column}, COUNT(id) as tc_count
        FROM test_cases
        WHERE {hash_column} != '' AND {hash_column} IS NOT NULL
        GROUP BY {hash_column}
        ORDER BY tc_count DESC
    ''')
    
    path_counts = cursor.fetchall()
    print(f"=== {path_type.upper()} PATHS ===")
    print(f"Total Unique {path_type.capitalize()} Paths: {len(path_counts)}")
    print(f"\n--- Top 10 {path_type.capitalize()} Paths by Testcase Count ---")
    for row in path_counts[:10]:
        print(f"Hash: {row[hash_column]} -> Testcases: {row['tc_count']}")
    
    # Fetch sequences for top paths to compute similarity
    top_hashes = [row[hash_column] for row in path_counts[:10]]
    sequences = {}
    
    for h in top_hashes:
        try:
            cursor.execute(f'SELECT sequence_json FROM {seq_table} WHERE {hash_column} = ?', (h,))
            row = cursor.fetchone()
            if row and row['sequence_json']:
                sequences[h] = json.loads(row['sequence_json'])
                # execution path sequences are dicts {func: [lines]}, we need to flatten them
                # or stable path is just a list of strings
                if isinstance(sequences[h], dict):
                    flat_seq = []
                    for k, v in sequences[h].items():
                        for line in v:
                            flat_seq.append(f"{k}:{line}")
                    sequences[h] = flat_seq
            else:
                sequences[h] = []
        except Exception as e:
            sequences[h] = []

    print("\n--- Pairwise Similarity (Top 5 vs Top 5) ---")
    top_5 = top_hashes[:5]
    print("Hash1            | Hash2            | Node Jaccard | Sequence Ratio")
    print("-" * 70)
    for i, h1 in enumerate(top_5):
        for j, h2 in enumerate(top_5):
            if i < j:
                j_sim = jaccard_similarity(sequences[h1], sequences[h2])
                s_sim = sequence_matcher_similarity(sequences[h1], sequences[h2])
                print(f"{h1[:16].ljust(16)} | {h2[:16].ljust(16)} | {j_sim:.4f}       | {s_sim:.4f}")

    conn.close()

if __name__ == "__main__":
    db = "kcov_coverage.db"
    if len(sys.argv) > 1:
        db = sys.argv[1]
    
    print("Starting Deep Path Analysis...\n")
    analyze_paths(db, 'stable')
    print("\n" + "="*70 + "\n")
    analyze_paths(db, 'execution')
