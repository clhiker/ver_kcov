"""
覆盖率数据库模块
使用 SQLite 存储测试用例、路径指纹、源码覆盖等信息
"""
import sqlite3
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TestCase:
    """测试用例信息"""
    id: int
    name: str
    path: str
    path_hash: str
    pc_count: int
    created_at: str


class CoverageDatabase:
    """覆盖率数据库管理类"""
    
    def __init__(self, db_path: str = "kcov_coverage.db"):
        self.db_path = db_path
        self.conn = None
        self._init_database()
    
    def _init_database(self):
        """初始化数据库连接和表结构"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # 支持字典访问
        self._create_tables()
    
    def _create_tables(self):
        """创建数据库表"""
        cursor = self.conn.cursor()
        
        # 测试用例表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL,
                path_hash TEXT NOT NULL,
                pc_count INTEGER,
                raw_pc_count INTEGER,
                compression_rate REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 路径指纹表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS path_fingerprints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path_hash TEXT UNIQUE NOT NULL,
                pcs TEXT NOT NULL,
                pc_count INTEGER,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 源码覆盖表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS source_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path_hash TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                function_name TEXT,
                pc_address TEXT,
                FOREIGN KEY (path_hash) REFERENCES path_fingerprints(path_hash)
            )
        ''')
        
        # 创建索引加速查询
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_path_hash ON test_cases(path_hash)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_source_file_line ON source_coverage(file_path, line_number)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_source_path_hash ON source_coverage(path_hash)
        ''')
        
        self.conn.commit()
    
    def save_test_case(self, name: str, path: str, path_hash: str, 
                      pc_count: int, raw_pc_count: int = 0, 
                      compression_rate: float = 0.0) -> int:
        """
        保存测试用例信息
        
        Returns:
            测试用例 ID
        """
        cursor = self.conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO test_cases 
                (name, path, path_hash, pc_count, raw_pc_count, compression_rate, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (name, path, path_hash, pc_count, raw_pc_count, compression_rate))
            
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            print(f"[!] Database error: {e}")
            self.conn.rollback()
            return -1
    
    def save_path_fingerprint(self, path_hash: str, pcs: List[str]) -> bool:
        """保存路径指纹"""
        cursor = self.conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO path_fingerprints 
                (path_hash, pcs, pc_count)
                VALUES (?, ?, ?)
            ''', (path_hash, json.dumps(pcs), len(pcs)))
            
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"[!] Database error: {e}")
            self.conn.rollback()
            return False
    
    def save_source_coverage(self, path_hash: str, file_path: str, 
                            line_number: int, function_name: str = "",
                            pc_address: str = "") -> bool:
        """保存源码覆盖信息"""
        cursor = self.conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO source_coverage 
                (path_hash, file_path, line_number, function_name, pc_address)
                VALUES (?, ?, ?, ?, ?)
            ''', (path_hash, file_path, line_number, function_name, pc_address))
            
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            # 可能是重复插入，忽略
            return False
    
    def batch_save_source_coverage(self, path_hash: str, locations: List[Dict]) -> int:
        """
        批量保存源码覆盖信息
        
        Args:
            path_hash: 路径哈希
            locations: [{file, line, function, address}] 列表
            
        Returns:
            成功插入的记录数
        """
        cursor = self.conn.cursor()
        count = 0
        
        try:
            data = []
            for loc in locations:
                data.append((
                    path_hash,
                    loc.get('file', ''),
                    loc.get('line', 0),
                    loc.get('function', ''),
                    loc.get('address', '')
                ))
            
            cursor.executemany('''
                INSERT OR IGNORE INTO source_coverage 
                (path_hash, file_path, line_number, function_name, pc_address)
                VALUES (?, ?, ?, ?, ?)
            ''', data)
            
            count = cursor.rowcount
            self.conn.commit()
            return count
        except sqlite3.Error as e:
            print(f"[!] Database error: {e}")
            self.conn.rollback()
            return 0
    
    def get_test_cases_by_path_hash(self, path_hash: str) -> List[TestCase]:
        """根据路径哈希获取测试用例"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM test_cases WHERE path_hash = ?
        ''', (path_hash,))
        
        return [self._row_to_test_case(row) for row in cursor.fetchall()]
    
    def get_path_fingerprint(self, path_hash: str) -> Optional[List[str]]:
        """获取路径指纹的 PC 序列"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT pcs FROM path_fingerprints WHERE path_hash = ?
        ''', (path_hash,))
        
        row = cursor.fetchone()
        if row:
            return json.loads(row['pcs'])
        return None
    
    def get_all_unique_paths(self) -> Dict[str, List[str]]:
        """获取所有唯一路径"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT path_hash, pcs FROM path_fingerprints')
        
        return {row['path_hash']: json.loads(row['pcs']) for row in cursor.fetchall()}
    
    def get_covered_lines_by_file(self, file_path: str) -> Set[int]:
        """获取指定文件被覆盖的行号（支持相对路径和模糊匹配）"""
        cursor = self.conn.cursor()
        
        # 支持模糊匹配：如果输入的是相对路径，匹配文件路径的结尾部分
        if not file_path.startswith('/'):
            # 相对路径，使用 LIKE 匹配
            cursor.execute('''
                SELECT DISTINCT line_number FROM source_coverage 
                WHERE file_path LIKE ?
            ''', (f'%{file_path}',))
        else:
            # 绝对路径，精确匹配
            cursor.execute('''
                SELECT DISTINCT line_number FROM source_coverage 
                WHERE file_path = ?
            ''', (file_path,))
        
        return {row['line_number'] for row in cursor.fetchall()}
    
    def get_all_covered_files(self) -> Set[str]:
        """获取所有被覆盖的文件"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT DISTINCT file_path FROM source_coverage')
        return {row['file_path'] for row in cursor.fetchall()}
    
    def get_coverage_statistics(self) -> dict:
        """获取覆盖率统计信息"""
        cursor = self.conn.cursor()
        
        stats = {}
        
        # 测试用例总数
        cursor.execute('SELECT COUNT(*) as count FROM test_cases')
        stats['total_test_cases'] = cursor.fetchone()['count']
        
        # 唯一路径数
        cursor.execute('SELECT COUNT(*) as count FROM path_fingerprints')
        stats['unique_paths'] = cursor.fetchone()['count']
        
        # 覆盖的源码行数
        cursor.execute('SELECT COUNT(DISTINCT file_path || ":" || line_number) as count FROM source_coverage')
        stats['covered_lines'] = cursor.fetchone()['count']
        
        # 覆盖的文件数
        cursor.execute('SELECT COUNT(DISTINCT file_path) as count FROM source_coverage')
        stats['covered_files'] = cursor.fetchone()['count']
        
        return stats
    
    def find_test_cases_for_line(self, file_path: str, line_number: int) -> List[str]:
        """找到覆盖指定源码行的所有测试用例"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT DISTINCT t.name 
            FROM source_coverage sc
            JOIN test_cases t ON sc.path_hash = t.path_hash
            WHERE sc.file_path = ? AND sc.line_number = ?
        ''', (file_path, line_number))
        
        return [row['name'] for row in cursor.fetchall()]
    
    def _row_to_test_case(self, row: sqlite3.Row) -> TestCase:
        """将数据库行转换为 TestCase 对象"""
        return TestCase(
            id=row['id'],
            name=row['name'],
            path=row['path'],
            path_hash=row['path_hash'],
            pc_count=row['pc_count'],
            created_at=row['created_at']
        )
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
