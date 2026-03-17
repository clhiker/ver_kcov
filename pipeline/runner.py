"""
自动化流水线控制器
协调整个覆盖率采集流程
"""
import os
import sys
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from datetime import datetime
from tqdm import tqdm
from multiprocessing import Pool, cpu_count

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import Config
from utils.terminal_format import format_table_row
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
            'unique_stable_paths': 0,
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
        
        # 清空旧数据
        print("\n[*] 清空数据库中的旧数据...")
        self.db.clear_all_data()
        
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
        
        # 从当前 fingerprints 收集唯一 PC
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
        print(f"[*] 覆盖源码行数（去重后）：{self.stats['covered_lines']}")
        
        # 显示本次运行的测试用例覆盖详情
        if all_fingerprints:
            print("\n" + "-"*60)
            print("[*] 本次运行的测试用例覆盖详情")
            print("-"*60)
            print(format_table_row([
                ("测试用例", 40, "left"),
                ("覆盖行数", 12, "right"),
                ("唯一行数", 12, "right"),
                ("状态", 10, "left"),
            ]))
            print("-"*70)
            
            # 只显示本次运行的测试用例
            for testcase_name, fingerprint in all_fingerprints.items():
                # 获取该测试用例的覆盖详情
                cursor = self.db.conn.cursor()
                
                # 该测试用例覆盖的唯一行数（去重）
                cursor.execute('''
                    SELECT COUNT(DISTINCT file_path || ":" || line_number) as count
                    FROM source_coverage
                    WHERE path_hash = ?
                ''', (fingerprint.path_id,))
                unique_lines = cursor.fetchone()['count']
                
                # 该测试用例覆盖的总行数（包含重复）
                cursor.execute('''
                    SELECT COUNT(*) as count
                    FROM source_coverage
                    WHERE path_hash = ?
                ''', (fingerprint.path_id,))
                covered_lines = cursor.fetchone()['count']
                
                # 标识状态
                status = "失败" if unique_lines == 0 or covered_lines == 0 else "成功"
                
                # 显示
                name = testcase_name
                if len(name) > 38:
                    name = name[:35] + "..."
                print(format_table_row([
                    (name, 40, "left"),
                    (covered_lines, 12, "right"),
                    (unique_lines, 12, "right"),
                    (status, 10, "left"),
                ]))
            print("-"*70)
        
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
                # 收集 KCOV 数据（即使 verifier 失败也会收集）
                raw_pcs = self.collector.collect(testcase)
                
                # 生成指纹（即使没有 PC 也会生成空指纹）
                fingerprint = self.fingerprinter.generate(raw_pcs)
                
                # 保存到数据库
                self.db.save_test_case(
                    name=Path(testcase).name,
                    path=testcase,
                    path_hash=fingerprint.path_id,
                    stable_path_hash="",
                    pc_count=fingerprint.pc_count,
                    raw_pc_count=fingerprint.raw_count,
                    compression_rate=fingerprint.compression_rate
                )
                
                # 保存唯一路径（如果有的话）
                if fingerprint.pc_count > 0:
                    self.db.save_path_fingerprint(fingerprint.path_id, fingerprint.pcs)
                
                all_fingerprints[Path(testcase).name] = fingerprint
                
            except Exception as e:
                print(f"\n[!] 处理 {Path(testcase).name} 失败：{e}")
                # 即使失败也保存一个空指纹，保证测试用例被记录
                all_fingerprints[Path(testcase).name] = PathFingerprint(
                    path_id="",
                    pcs=[],
                    pc_count=0,
                    raw_count=0,
                    compression_rate=0.0,
                    stable_path_id="",
                    stable_sequence=[]
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
        """收集所有唯一 PC 地址（仅从当前 fingerprints）"""
        unique_pcs = set()
        
        for fingerprint in fingerprints.values():
            if fingerprint.pcs:
                unique_pcs.update(fingerprint.pcs)
        
        return unique_pcs
    
    def _save_to_database(self, fingerprints: Dict[str, PathFingerprint]):
        """解析源码位置并保存到数据库"""
        # 步骤 1: 按测试用例组织需要解析的 PC
        testcase_to_pcs = {}
        all_pcs_needed = set()
        
        for testcase_name, fingerprint in fingerprints.items():
            if not fingerprint.pcs:
                continue
            testcase_to_pcs[testcase_name] = fingerprint.pcs
            all_pcs_needed.update(fingerprint.pcs)
        
        # 步骤 2: 批量解析所有 PC（利用查找表 + 补充解析）
        # 检查查找表覆盖情况
        pcs_in_table = set(self.resolver._lookup_table.keys())
        pcs_missing = all_pcs_needed - pcs_in_table
        
        # 如果有缺失的 PC，补充解析
        if pcs_missing:
            missing_locations = self.resolver._run_batch_llvm_symbolizer(sorted(list(pcs_missing)))
            # 合并到查找表
            self.resolver._lookup_table.update(missing_locations)
        
        normalized_sequences = self._build_normalized_verifier_sequences(testcase_to_pcs)
        stable_sequences = self._build_stable_path_sequences(normalized_sequences)
        stable_path_ids: Set[str] = set()

        # 步骤 3: 为每个测试用例独立保存源码覆盖信息
        for testcase_name, pcs in tqdm(testcase_to_pcs.items(), desc="保存测试用例覆盖"):
            # 获取测试用例 ID
            cursor = self.db.conn.cursor()
            cursor.execute('SELECT id FROM test_cases WHERE name = ?', (testcase_name,))
            row = cursor.fetchone()
            if not row:
                print(f"[!] 测试用例 {testcase_name} 未找到，跳过...")
                continue
            
            testcase_id = row['id']
            fingerprint = fingerprints[testcase_name]
            path_id = fingerprint.path_id
            fingerprint.stable_sequence = stable_sequences.get(testcase_name, [])
            fingerprint.stable_path_id = self.fingerprinter.compute_stable_hash(fingerprint.stable_sequence) if fingerprint.stable_sequence else ""
            if fingerprint.stable_path_id:
                stable_path_ids.add(fingerprint.stable_path_id)

            cursor.execute('''
                UPDATE test_cases
                SET stable_path_hash = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (fingerprint.stable_path_id, testcase_id))
            self.db.conn.commit()
            
            # 直接从查找表获取该测试用例的源码位置
            locations = []
            for pc in pcs:
                if pc in self.resolver._lookup_table:
                    locations.extend(self.resolver._lookup_table[pc])
            
            # 转换为字典格式
            loc_dicts = [loc.to_dict() for loc in locations if loc.file and loc.line > 0]
            
            # 批量保存（按 testcase_id 保存）
            if loc_dicts:
                self.db.batch_save_source_coverage(testcase_id, path_id, loc_dicts)

            sequence = self._build_execution_path_sequence(pcs)
            if sequence:
                self.db.save_execution_path_sequence(path_id, sequence)

            if fingerprint.stable_path_id and fingerprint.stable_sequence:
                self.db.save_stable_path_sequence(fingerprint.stable_path_id, fingerprint.stable_sequence)

        self.stats['unique_stable_paths'] = len(stable_path_ids)

    def _build_execution_path_sequence(self, pcs: List[str]) -> Dict[str, List[int]]:
        """按原始 PC 顺序构建最内层源码行轨迹。"""
        sequences: Dict[str, List[int]] = {}
        last_seen: Dict[str, int] = {}

        for pc in pcs:
            locations = self.resolver._lookup_table.get(pc, [])
            if not locations:
                continue

            loc = locations[0]
            if not loc.file or loc.line <= 0:
                continue

            if last_seen.get(loc.file) == loc.line:
                continue

            sequences.setdefault(loc.file, []).append(loc.line)
            last_seen[loc.file] = loc.line

        return sequences

    def _build_normalized_verifier_sequences(self, testcase_to_pcs: Dict[str, List[str]]) -> Dict[str, List[Tuple[str, int]]]:
        """将原始 PC 序列归一化为 verifier.c 内的事件序列。"""
        sequences: Dict[str, List[Tuple[str, int]]] = {}

        for testcase_name, pcs in testcase_to_pcs.items():
            normalized: List[Tuple[str, int]] = []
            last_event: Optional[Tuple[str, int]] = None

            for pc in pcs:
                locations = self.resolver._lookup_table.get(pc, [])
                if not locations:
                    continue

                loc = locations[0]
                if not loc.file.endswith("kernel/bpf/verifier.c") or loc.line <= 0:
                    continue

                event = (loc.function or "?", loc.line)
                if event == last_event:
                    continue

                normalized.append(event)
                last_event = event

            sequences[testcase_name] = normalized

        return sequences

    def _build_stable_path_sequences(self, normalized_sequences: Dict[str, List[Tuple[str, int]]]) -> Dict[str, List[str]]:
        """提取跨运行更稳定的控制流骨架。"""
        predecessors: Dict[Tuple[str, int], Set[Tuple[str, int]]] = {}
        successors: Dict[Tuple[str, int], Set[Tuple[str, int]]] = {}
        line_bucket = max(1, int(self.config.stable_path_line_bucket))

        for sequence in normalized_sequences.values():
            for event in sequence:
                predecessors.setdefault(event, set())
                successors.setdefault(event, set())
            for current_event, next_event in zip(sequence, sequence[1:]):
                successors.setdefault(current_event, set()).add(next_event)
                predecessors.setdefault(next_event, set()).add(current_event)

        stable_sequences: Dict[str, List[str]] = {}
        for testcase_name, sequence in normalized_sequences.items():
            anchors: List[str] = []
            seen_anchors: Set[str] = set()

            for index, event in enumerate(sequence):
                prev_event = sequence[index - 1] if index > 0 else None
                next_event = sequence[index + 1] if index + 1 < len(sequence) else None

                is_anchor = (
                    index == 0
                    or index == len(sequence) - 1
                    or (prev_event is not None and prev_event[0] != event[0])
                    or (next_event is not None and next_event[0] != event[0])
                    or len(predecessors.get(event, set())) > 1
                    or len(successors.get(event, set())) > 1
                )

                if not is_anchor:
                    continue

                bucketed_line = (event[1] // line_bucket) * line_bucket
                anchor = f"{event[0]}:{bucketed_line}"
                if anchor not in seen_anchors:
                    anchors.append(anchor)
                    seen_anchors.add(anchor)

            stable_sequences[testcase_name] = anchors

        return stable_sequences
    
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
