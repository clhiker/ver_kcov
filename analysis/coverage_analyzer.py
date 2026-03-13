"""
覆盖率分析模块
分析覆盖率数据、路径聚类、生成报告
"""
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict

from core.coverage_db import CoverageDatabase


@dataclass
class CoverageReport:
    """覆盖率报告"""
    total_test_cases: int
    unique_paths: int
    covered_files: int
    covered_lines: int
    total_lines: int = 0  # 需要外部提供
    coverage_percentage: float = 0.0
    
    # 等价测试用例分组
    equivalent_groups: Dict[str, List[str]] = None
    
    # 未覆盖的行
    uncovered_lines: Dict[str, List[int]] = None
    
    def to_dict(self) -> dict:
        return {
            'total_test_cases': self.total_test_cases,
            'unique_paths': self.unique_paths,
            'covered_files': self.covered_files,
            'covered_lines': self.covered_lines,
            'total_lines': self.total_lines,
            'coverage_percentage': self.coverage_percentage,
            'equivalent_groups': self.equivalent_groups or {},
            'uncovered_lines': self.uncovered_lines or {}
        }


class CoverageAnalyzer:
    """覆盖率分析器"""
    
    def __init__(self, db: CoverageDatabase):
        self.db = db
    
    def generate_report(self, source_files: Optional[List[str]] = None) -> CoverageReport:
        """
        生成覆盖率报告
        
        Args:
            source_files: 可选的源文件列表，用于计算覆盖率百分比
            
        Returns:
            CoverageReport 对象
        """
        # 获取基本统计
        stats = self.db.get_coverage_statistics()
        
        report = CoverageReport(
            total_test_cases=stats['total_test_cases'],
            unique_paths=stats['unique_paths'],
            covered_files=stats['covered_files'],
            covered_lines=stats['covered_lines']
        )
        
        # 获取等价测试用例分组
        report.equivalent_groups = self.db.get_equivalent_test_cases()
        
        # 如果提供了源文件，计算未覆盖的行
        if source_files:
            report.uncovered_lines = self._find_uncovered_lines(source_files)
            
            # 计算总行数和覆盖率
            total_lines = sum(len(lines) for lines in report.uncovered_lines.values())
            total_lines += report.covered_lines
            report.total_lines = total_lines
            
            if total_lines > 0:
                report.coverage_percentage = (report.covered_lines / total_lines) * 100
        
        return report
    
    def _find_uncovered_lines(self, source_files: List[str]) -> Dict[str, List[int]]:
        """
        找出未覆盖的源码行
        
        Returns:
            {file_path: [uncovered_line_numbers]}
        """
        uncovered = {}
        
        for file_path in source_files:
            if not Path(file_path).exists():
                continue
            
            # 读取源文件
            with open(file_path, 'r') as f:
                total_lines = len(f.readlines())
            
            # 获取已覆盖的行
            covered = self.db.get_covered_lines_by_file(file_path)
            
            # 计算未覆盖的行
            all_lines = set(range(1, total_lines + 1))
            uncovered[file_path] = sorted(list(all_lines - covered))
        
        return uncovered
    
    def analyze_path_distribution(self) -> dict:
        """
        分析路径分布
        
        Returns:
            路径分布统计
        """
        all_paths = self.db.get_all_unique_paths()
        
        distribution = {
            'very_short': 0,    # < 10 PCs
            'short': 0,         # 10-50 PCs
            'medium': 0,        # 50-200 PCs
            'long': 0,          # 200-1000 PCs
            'very_long': 0      # > 1000 PCs
        }
        
        for path_hash, pcs in all_paths.items():
            pc_count = len(pcs)
            
            if pc_count < 10:
                distribution['very_short'] += 1
            elif pc_count < 50:
                distribution['short'] += 1
            elif pc_count < 200:
                distribution['medium'] += 1
            elif pc_count < 1000:
                distribution['long'] += 1
            else:
                distribution['very_long'] += 1
        
        return distribution
    
    def find_hot_paths(self, top_n: int = 10) -> List[Tuple[str, int]]:
        """
        找出最常见的路径（被最多测试用例触发）
        
        Returns:
            [(path_hash, test_case_count)]
        """
        equivalent_groups = self.db.get_equivalent_test_cases()
        
        # 按测试用例数排序
        sorted_paths = sorted(
            equivalent_groups.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )
        
        return sorted_paths[:top_n]
    
    def find_cold_paths(self, top_n: int = 10) -> List[str]:
        """
        找出最冷门的路径（只有 1 个测试用例触发）
        
        Returns:
            [path_hash]
        """
        all_paths = self.db.get_all_unique_paths()
        equivalent_groups = self.db.get_equivalent_test_cases()
        
        # 只出现一次的路径
        cold_paths = [
            path_hash for path_hash in all_paths.keys()
            if path_hash not in equivalent_groups
        ]
        
        return cold_paths[:top_n]
    
    def get_file_coverage_summary(self, file_path: str) -> dict:
        """
        获取单个文件的覆盖率摘要
        
        Args:
            file_path: 源文件路径
            
        Returns:
            覆盖率摘要字典
        """
        covered_lines = self.db.get_covered_lines_by_file(file_path)
        
        if not Path(file_path).exists():
            return {'error': 'File not found'}
        
        with open(file_path, 'r') as f:
            total_lines = len(f.readlines())
        
        covered_count = len(covered_lines)
        uncovered_count = total_lines - covered_count
        coverage_pct = (covered_count / total_lines * 100) if total_lines > 0 else 0.0
        
        return {
            'file': file_path,
            'total_lines': total_lines,
            'covered_lines': covered_count,
            'uncovered_lines': uncovered_count,
            'coverage_percentage': coverage_pct,
            'covered_line_numbers': sorted(list(covered_lines))
        }
    
    def export_report(self, output_path: str, format: str = 'json'):
        """
        导出报告
        
        Args:
            output_path: 输出文件路径
            format: 导出格式（json, text）
        """
        report = self.generate_report()
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        if format == 'json':
            with open(output_path, 'w') as f:
                json.dump(report.to_dict(), f, indent=2)
        elif format == 'text':
            self._export_text_report(output_path, report)
    
    def _export_text_report(self, output_path: str, report: CoverageReport):
        """导出文本格式报告"""
        with open(output_path, 'w') as f:
            f.write("="*60 + "\n")
            f.write("Verifier 覆盖率分析报告\n")
            f.write("="*60 + "\n\n")
            
            f.write(f"测试用例总数：{report.total_test_cases}\n")
            f.write(f"唯一路径数：{report.unique_paths}\n")
            f.write(f"覆盖文件数：{report.covered_files}\n")
            f.write(f"覆盖行数：{report.covered_lines}\n")
            
            if report.total_lines > 0:
                f.write(f"覆盖率：{report.coverage_percentage:.2f}%\n")
            
            f.write("\n")
            
            if report.equivalent_groups:
                f.write(f"等价测试用例组数：{len(report.equivalent_groups)}\n")
            
            f.write("\n" + "="*60 + "\n")
