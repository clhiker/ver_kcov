"""
数据库迁移脚本
为 source_coverage 表添加 testcase_id 字段并更新现有数据
"""
import sqlite3
import sys
from pathlib import Path

def migrate_database(db_path: str):
    """迁移数据库"""
    print(f"[*] 开始迁移数据库：{db_path}")
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # 检查是否已经迁移过
        cursor.execute("PRAGMA table_info(source_coverage)")
        columns = [col['name'] for col in cursor.fetchall()]
        
        if 'testcase_id' in columns:
            print("[*] 数据库已经迁移过，无需重复操作")
            return
        
        print("[*] 检测到旧版数据库结构，开始迁移...")
        
        # 1. 备份旧表
        print("[*] 备份旧表...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS source_coverage_old AS 
            SELECT * FROM source_coverage
        ''')
        
        # 2. 删除旧表
        print("[*] 删除旧表...")
        cursor.execute('DROP TABLE source_coverage')
        
        # 3. 创建新表（包含 testcase_id）
        print("[*] 创建新表...")
        cursor.execute('''
            CREATE TABLE source_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                testcase_id INTEGER NOT NULL,
                path_hash TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                function_name TEXT,
                pc_address TEXT,
                FOREIGN KEY (testcase_id) REFERENCES test_cases(id),
                FOREIGN KEY (path_hash) REFERENCES path_fingerprints(path_hash)
            )
        ''')
        
        # 4. 创建索引
        print("[*] 创建索引...")
        cursor.execute('''
            CREATE INDEX idx_source_file_line ON source_coverage(file_path, line_number)
        ''')
        cursor.execute('''
            CREATE INDEX idx_source_path_hash ON source_coverage(path_hash)
        ''')
        cursor.execute('''
            CREATE INDEX idx_source_testcase_id ON source_coverage(testcase_id)
        ''')
        
        # 5. 迁移数据：根据 path_hash 关联 test_cases，为每条记录分配 testcase_id
        print("[*] 迁移数据...")
        cursor.execute('''
            INSERT INTO source_coverage (testcase_id, path_hash, file_path, line_number, function_name, pc_address)
            SELECT tc.id, sc.path_hash, sc.file_path, sc.line_number, sc.function_name, sc.pc_address
            FROM source_coverage_old sc
            JOIN test_cases tc ON sc.path_hash = tc.path_hash
        ''')
        
        migrated_count = cursor.rowcount
        print(f"[*] 成功迁移 {migrated_count} 条记录")
        
        # 6. 删除备份表
        print("[*] 清理备份表...")
        cursor.execute('DROP TABLE source_coverage_old')
        
        conn.commit()
        print("[*] 迁移完成！")
        
    except Exception as e:
        print(f"[!] 迁移失败：{e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "kcov_coverage.db"
    
    if not Path(db_path).exists():
        print(f"[!] 数据库文件不存在：{db_path}")
        sys.exit(1)
    
    migrate_database(db_path)
