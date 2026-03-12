"""
自动化流水线控制器
协调整个覆盖率采集流程
"""
import os
import sys
from pathlib import Path
from typing import List, Dict, Set, Optional
from datetime import datetime
from multiprocessing import Pool, cpu_count

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import Config
from core.kcov_collector import KCOVCollector
from core.path_fingerprinter import PathFingerprinter, PathFingerprint
from core.pc_resolver import PCResolver, SourceLocation
from core.coverage_db import CoverageDatabase


class CoveragePipeline:
    """覆盖率采集流水线"""
    
    def __init__(self, config: Config):
        self.config = config
        self.collector = KCOVCollector(config)
        self.fingerprinter = PathFingerprinter(config)
        self.resolver = PCResolver(config)
        self.db = CoverageDatabase(config.db_path)
        
        # 统计信息
        self.stats = {
            'total_testcases': 0,
            'successful': 0,
            'failed': 0,
            'unique_paths': 0,
            'covered_lines': 0
        }
    
    def run(self, testcase_dir: Optional[str] = None, 
            parallel: bool = False,
            workers: int = 0) -> dict:
        """
        运行完整的覆盖率采集流水线
        
        流程：
        1. 发现测试用例
        2. 收集 KCOV 数据
        3. 生成路径指纹
        4. 构建全局 PC 查找表
        5. 解析源码位置
        6. 保存到数据库
        
        Args:
            testcase_dir: 测试用例目录，如果为 None 则使用配置中的目录
            parallel: 是否并行处理
            workers: 工作进程数
            
        Returns:
            统计信息字典
        """
        testcase_dir = testcase_dir or self.config.testcase_dir
        start_time = datetime.now()
        
        print(f"[*] 开始覆盖率采集流水线")
        print(f"[*] 测试用例目录：{testcase_dir}")
        print(f"[*] 并行模式：{'开启' if parallel else '关闭'}")
        
        # 步骤 1: 发现测试用例
        testcases = self._discover_testcases(testcase_dir)
        self.stats['total_testcases'] = len(testcases)
        print(f"[*] 发现 {len(testcases)} 个测试用例")
        
        if not testcases:
            print("[!] 没有找到测试用例")
            return self.stats
        
        # 步骤 2 & 3: 收集 KCOV 数据并生成指纹
        print("\n[*] 阶段 1: 收集 KCOV 数据并生成路径指纹")
        all_fingerprints: Dict[str, PathFingerprint] = {}
        
        if parallel and len(testcases) > 1:
            all_fingerprints = self._collect_parallel(testcases, workers or self.config.parallel_workers)
        else:
            all_fingerprints = self._collect_sequential(testcases)
        
        # 统计成功/失败
        self.stats['successful'] = len([fp for fp in all_fingerprints.values() if fp.pc_count > 0])
        self.stats['failed'] = self.stats['total_testcases'] - self.stats['successful']
        
        # 步骤 4: 构建全局 PC 查找表
        print("\n[*] 阶段 2: 构建全局 PC 查找表")
        unique_pcs = self._collect_all_unique_pcs(all_fingerprints)
        self.stats['unique_paths'] = len(unique_pcs)
        print(f"[*] 发现 {len(unique_pcs)} 个唯一 PC 地址")
        
        if unique_pcs:
            lookup_table = self.resolver.build_lookup_table(
                unique_pcs, 
                self.config.lookup_table_cache
            )
            print(f"[*] 已构建包含 {len(lookup_table)} 条记录的查找表")
        
        # 步骤 5 & 6: 解析源码并保存到数据库
        print("\n[*] 阶段 3: 解析源码位置并保存到数据库")
        self._save_to_database(all_fingerprints)
        
        # 计算覆盖的行数
        db_stats = self.db.get_coverage_statistics()
        self.stats['covered_lines'] = db_stats.get('covered_lines', 0)
        
        # 完成
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        print("\n" + "="*60)
        print("[*] 覆盖率采集完成")
        print("="*60)
        print(f"[*] 总测试用例数：{self.stats['total_testcases']}")
        print(f"[*] 成功：{self.stats['successful']}")
        print(f"[*] 失败：{self.stats['failed']}")
        print(f"[*] 唯一路径数：{self.stats['unique_paths']}")
        print(f"[*] 覆盖源码行数：{self.stats['covered_lines']}")
        print(f"[*] 耗时：{duration:.2f} 秒")
        print("="*60)
        
        return self.stats
    
    def _discover_testcases(self, testcase_dir: str) -> List[str]:
        """发现测试用例文件"""
        testcases = []
        test_dir = Path(testcase_dir)
        
        if not test_dir.exists():
            print(f"[!] 测试用例目录不存在：{testcase_dir}")
            return testcases
        
        # 查找所有 .o 文件
        for f in test_dir.glob("*.o"):
            testcases.append(str(f))
        
        return sorted(testcases)
    
    def _collect_sequential(self, testcases: List[str]) -> Dict[str, PathFingerprint]:
        """串行收集"""
        all_fingerprints = {}
        
        for i, testcase in enumerate(testcases, 1):
            print(f"\r[{i}/{len(testcases)}] 处理 {Path(testcase).name}...", end='', flush=True)
            
            try:
                # 收集 KCOV 数据
                raw_pcs = self.collector.collect(testcase)
                
                # 生成指纹
                fingerprint = self.fingerprinter.generate(raw_pcs)
                
                # 保存到数据库
                self.db.save_test_case(
                    name=Path(testcase).name,
                    path=testcase,
                    path_hash=fingerprint.path_id,
                    pc_count=fingerprint.pc_count,
                    raw_pc_count=fingerprint.raw_count,
                    compression_rate=fingerprint.compression_rate
                )
                
                # 保存唯一路径
                self.db.save_path_fingerprint(fingerprint.path_id, fingerprint.pcs)
                
                all_fingerprints[Path(testcase).name] = fingerprint
                
            except Exception as e:
                print(f"\n[!] 处理 {Path(testcase).name} 失败：{e}")
                all_fingerprints[Path(testcase).name] = PathFingerprint(
                    path_id="",
                    pcs=[],
                    pc_count=0,
                    raw_count=0,
                    compression_rate=0.0
                )
        
        print()  # 换行
        return all_fingerprints
    
    def _collect_parallel(self, testcases: List[str], workers: int) -> Dict[str, PathFingerprint]:
        """并行收集（简化版本）"""
        print(f"[*] 使用 {workers} 个工作进程并行处理")
        # TODO: 实现并行收集逻辑
        # 由于 KCOV 采集需要运行内核程序，并行可能需要特殊处理
        # 这里暂时退化为串行
        return self._collect_sequential(testcases)
    
    def _collect_all_unique_pcs(self, fingerprints: Dict[str, PathFingerprint]) -> Set[str]:
        """收集所有唯一 PC 地址"""
        unique_pcs = set()
        
        for fingerprint in fingerprints.values():
            if fingerprint.pcs:
                unique_pcs.update(fingerprint.pcs)
        
        return unique_pcs
    
    def _save_to_database(self, fingerprints: Dict[str, PathFingerprint]):
        """解析源码位置并保存到数据库"""
        for testcase_name, fingerprint in fingerprints.items():
            if not fingerprint.pcs:
                continue
            
            # 解析路径
            locations = self.resolver.resolve_path(fingerprint.pcs)
            
            # 转换为字典格式
            loc_dicts = [loc.to_dict() for loc in locations if loc.file and loc.line > 0]
            
            # 批量保存
            if loc_dicts:
                self.db.batch_save_source_coverage(fingerprint.path_id, loc_dicts)
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return self.stats.copy()
    
    def close(self):
        """关闭资源"""
        self.db.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def main():
    """主函数"""
    # 加载配置
    config_path = Path(__file__).parent.parent / "config" / "kcov_config.yaml"
    
    if config_path.exists():
        config = Config.from_yaml(str(config_path))
    else:
        config = Config()
        print("[!] 配置文件不存在，使用默认配置")
    
    # 验证配置
    if not config.validate():
        print("[!] 配置验证失败")
        sys.exit(1)
    
    # 运行流水线
    with CoveragePipeline(config) as pipeline:
        stats = pipeline.run(parallel=False)
        
        if stats['failed'] > 0:
            print(f"\n[!] 有 {stats['failed']} 个测试用例处理失败")
            sys.exit(1)


if __name__ == "__main__":
    main()
