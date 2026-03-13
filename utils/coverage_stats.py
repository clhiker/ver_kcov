"""
覆盖率统计工具
计算 verifier 的总 PC 数、总代码行数和覆盖率
"""
import sqlite3
from pathlib import Path


def get_verifier_stats(db_path: str = "kcov_coverage.db"):
    """获取 verifier 覆盖率统计信息"""
    
    # 连接数据库
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    stats = {}
    
    # 1. 已采集的唯一 PC 数量
    cursor.execute("""
        SELECT COUNT(DISTINCT pc_address) 
        FROM source_coverage 
        WHERE pc_address IS NOT NULL AND pc_address != ''
    """)
    stats['collected_unique_pcs'] = cursor.fetchone()[0]
    
    # 2. 已覆盖的唯一源码行数
    cursor.execute("""
        SELECT COUNT(DISTINCT file_path || ':' || line_number)
        FROM source_coverage
        WHERE file_path LIKE '%verifier.c'
    """)
    stats['covered_source_lines'] = cursor.fetchone()[0]
    
    # 3. 已覆盖的文件数
    cursor.execute("""
        SELECT COUNT(DISTINCT file_path)
        FROM source_coverage
        WHERE file_path LIKE '%verifier.c'
    """)
    stats['covered_files'] = cursor.fetchone()[0]
    
    # 4. 路径指纹数量（不同路径的数量）
    cursor.execute("SELECT COUNT(*) FROM path_fingerprints")
    stats['total_paths'] = cursor.fetchone()[0]
    
    # 5. 测试用例总数
    cursor.execute("SELECT COUNT(*) FROM test_cases")
    stats['total_test_cases'] = cursor.fetchone()[0]
    
    # 6. 每个路径的详细信息
    cursor.execute("""
        SELECT path_hash, pc_count, pcs 
        FROM path_fingerprints
    """)
    paths_info = []
    for row in cursor.fetchall():
        import json
        pcs = json.loads(row['pcs'])
        paths_info.append({
            'path_hash': row['path_hash'],
            'pc_count': row['pc_count'],
            'unique_pcs_in_path': len(set(pcs))
        })
    stats['paths_details'] = paths_info
    
    # 7. 所有路径中的唯一 PC 总数（去重）
    all_pcs = set()
    for path in paths_info:
        import json
        pcs = json.loads(cursor.execute(
            "SELECT pcs FROM path_fingerprints WHERE path_hash = ?", 
            (path['path_hash'],)
        ).fetchone()[0])
        all_pcs.update(pcs)
    stats['total_unique_pcs_in_paths'] = len(all_pcs)
    
    conn.close()
    
    # 8. verifier 总代码行数（从配置文件估算）
    # 这是固定值
    stats['verifier_total_lines'] = 26169  # 实际 wc -l 的结果
    
    # 9. verifier 地址范围
    stats['verifier_start_addr'] = '0xffffffff81dcd390'
    stats['verifier_end_addr'] = '0xffffffff81e17390'
    stats['verifier_address_space'] = 303104  # 字节
    
    # 10. 预估的总 PC 数量（理论最大值）
    stats['estimated_total_pcs'] = stats['verifier_address_space'] // 4  # 假设平均指令 4 字节
    
    # 11. 计算覆盖率
    if stats['verifier_total_lines'] > 0:
        stats['line_coverage'] = (stats['covered_source_lines'] / stats['verifier_total_lines']) * 100
    else:
        stats['line_coverage'] = 0.0
    
    if stats['estimated_total_pcs'] > 0:
        stats['pc_coverage'] = (stats['collected_unique_pcs'] / stats['estimated_total_pcs']) * 100
    else:
        stats['pc_coverage'] = 0.0
    
    return stats


def print_stats(stats: dict):
    """打印统计信息"""
    print("="*70)
    print("Verifier 覆盖率统计报告")
    print("="*70)
    
    print("\n【代码规模】")
    print(f"  Verifier 总代码行数：{stats['verifier_total_lines']:,} 行")
    print(f"  Verifier 地址空间：{stats['verifier_address_space']:,} 字节")
    print(f"  预估总 PC 数量：{stats['estimated_total_pcs']:,} 个")
    
    print("\n【覆盖情况】")
    print(f"  已采集唯一 PC 数：{stats['collected_unique_pcs']:,} 个")
    print(f"  已覆盖源码行数：{stats['covered_source_lines']:,} 行")
    print(f"  已覆盖文件数：{stats['covered_files']} 个")
    
    print("\n【覆盖率】")
    print(f"  代码行覆盖率：{stats['line_coverage']:.4f}%")
    print(f"  PC 覆盖率：{stats['pc_coverage']:.4f}%")
    
    print("\n【路径统计】")
    print(f"  路径指纹总数：{stats['total_paths']} 条")
    print(f"  测试用例总数：{stats['total_test_cases']} 个")
    print(f"  所有路径中的唯一 PC 总数：{stats['total_unique_pcs_in_paths']:,} 个")
    
    if stats['paths_details']:
        print("\n【路径详情】")
        print(f"  {'路径哈希':<18} {'PC 数量':>10} {'唯一 PC 数':>12}")
        print("  " + "-"*42)
        for path in stats['paths_details']:
            print(f"  {path['path_hash']:<18} {path['pc_count']:>10} {path['unique_pcs_in_path']:>12}")
    
    print("\n" + "="*70)


if __name__ == "__main__":
    db_path = "kcov_coverage.db"
    if not Path(db_path).exists():
        print(f"[!] 数据库文件不存在：{db_path}")
        exit(1)
    
    stats = get_verifier_stats(db_path)
    print_stats(stats)
