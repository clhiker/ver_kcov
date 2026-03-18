"""
并发执行流水线控制器
"""
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor, as_completed

from pipeline.runner import CoveragePipeline
from core.path_fingerprinter import PathFingerprinter, PathFingerprint
from core.kcov_collector import KCOVCollector
from core.coverage_db import CoverageDatabase
from utils.config import Config


class ParallelKCOVCollector(KCOVCollector):
    """支持挂载名称注入的并行 KCOV 采集器"""
    def collect(self, testcase_path: str, output_file: Optional[str] = None, prog_name: str = None) -> List[str]:
        if not self.kcov_runner.exists():
            raise FileNotFoundError(f"KCOV runner not found: {self.kcov_runner}")
        
        fd, temp_output_file = tempfile.mkstemp(prefix=f"kcov_{prog_name}_" if prog_name else "kcov_", suffix=".txt")
        os.close(fd)
        
        actual_output_file = output_file if output_file else temp_output_file
        if os.path.exists(actual_output_file):
            os.remove(actual_output_file)
            
        cmd = [str(self.kcov_runner), testcase_path, "-o", actual_output_file]
        if prog_name:
            cmd.extend(["-n", prog_name])
            
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, check=False)
            if os.path.exists(actual_output_file):
                pcs = self._read_pcs_from_file(actual_output_file)
            else:
                pcs = self._parse_pcs_from_stdout(result.stdout)
            return pcs
        except subprocess.TimeoutExpired:
            print(f"[WARNING] KCOV collection timeout for {testcase_path}")
            return []
        except Exception as e:
            print(f"[WARNING] 收集 {testcase_path} 时出错：{e}")
            return []
        finally:
            if not output_file and os.path.exists(actual_output_file):
                os.remove(actual_output_file)


def _parallel_collect_worker(testcase: str, config: Config) -> Tuple[str, PathFingerprint]:
    """多进程 worker：运行单用例的 KCOV 采集，需重建独立对象"""
    collector = ParallelKCOVCollector(config)
    fingerprinter = PathFingerprinter(config)
    
    basename = Path(testcase).stem
    
    try:
        raw_pcs = collector.collect(testcase, prog_name=basename)
        fingerprint = fingerprinter.generate(raw_pcs)
        return Path(testcase).name, fingerprint
    except Exception as e:
        print(f"\n[!] 处理 {Path(testcase).name} 失败：{e}")
        return Path(testcase).name, PathFingerprint("", [], 0, 0, 0.0, "", [])


def _parallel_db_write_worker(testcase_name: str, path_id: str,
                            stable_sequence: List[str], stable_path_id: str,
                            sequence: Dict[str, List[int]],
                            locations_dicts: List[Dict],
                            config: Config) -> bool:
    """
    独立进程写库工作者
    """
    # 短暂打开数据库
    db = CoverageDatabase(config.db_path)
    try:
        # sqlite3 在高并发下必须依靠 internal lock 串行化，可以通过设置 timeout 防止 database is locked
        # Python sqlite3 driver 默认连接带有 timeout=5.0
        cursor = db.conn.cursor()
        cursor.execute('SELECT id FROM test_cases WHERE name = ?', (testcase_name,))
        row = cursor.fetchone()
        if not row:
            return False
            
        testcase_id = row['id']
        
        cursor.execute('''
            UPDATE test_cases
            SET stable_path_hash = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (stable_path_id, testcase_id))
        db.conn.commit()
        
        if locations_dicts:
            db.batch_save_source_coverage(testcase_id, path_id, locations_dicts)
            
        if sequence:
            db.save_execution_path_sequence(path_id, sequence)
            
        if stable_path_id and stable_sequence:
            db.save_stable_path_sequence(stable_path_id, stable_sequence)
            
        return True
    finally:
        db.close()


class ParallelCoveragePipeline(CoveragePipeline):
    """覆盖率采集流水线（并行版本）"""
    
    def _collect_parallel(self, testcases: List[str], workers: int) -> Dict[str, PathFingerprint]:
        """并行收集"""
        print(f"[*] 使用 {workers} 个工作进程并行处理 (挂载收集)")
        all_fingerprints = {}
        
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_parallel_collect_worker, tc, self.config): tc
                for tc in testcases
            }
            
            for future in tqdm(as_completed(futures), total=len(testcases), desc="并行收集 KCOV 数据"):
                tc_name, fingerprint = future.result()
                all_fingerprints[tc_name] = fingerprint
                
                # 在主进程中串行插入测试用例基本信息，避免 sqlite locked
                tc_path = futures[future]
                self.db.save_test_case(
                    name=tc_name,
                    path=tc_path,
                    path_hash=fingerprint.path_id,
                    stable_path_hash="",
                    pc_count=fingerprint.pc_count,
                    raw_pc_count=fingerprint.raw_count,
                    compression_rate=fingerprint.compression_rate
                )
                if fingerprint.pc_count > 0:
                    self.db.save_path_fingerprint(fingerprint.path_id, fingerprint.pcs)
                    
        print()  # 换行
        return all_fingerprints

    def _save_to_database(self, fingerprints: Dict[str, PathFingerprint]):
        """解析源码位置并保存到数据库（多进程并行保存）"""
        testcase_to_pcs = {}
        all_pcs_needed = set()
        
        for testcase_name, fingerprint in fingerprints.items():
            if not fingerprint.pcs:
                continue
            testcase_to_pcs[testcase_name] = fingerprint.pcs
            all_pcs_needed.update(fingerprint.pcs)
        
        # 1. 查找表构建与补充解析依然在主进程执行（llvm-symbolizer 支持 batch 处理）
        pcs_in_table = set(self.resolver._lookup_table.keys())
        pcs_missing = all_pcs_needed - pcs_in_table
        
        if pcs_missing:
            missing_locations = self.resolver._run_batch_llvm_symbolizer(sorted(list(pcs_missing)))
            self.resolver._lookup_table.update(missing_locations)
        
        # 获取序列
        normalized_sequences = self._build_normalized_verifier_sequences(testcase_to_pcs)
        stable_sequences = self._build_stable_path_sequences(normalized_sequences)
        stable_path_ids: Set[str] = set()
        
        workers = getattr(self.config, 'parallel_workers', max(1, os.cpu_count() - 1))
        print(f"\n[*] 使用 {workers} 个工作进程并行处理 (数据库持久化)")

        # 2. 将入库操作放在 ProcessPool 中并发
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = []
            
            for testcase_name, pcs in testcase_to_pcs.items():
                fingerprint = fingerprints[testcase_name]
                path_id = fingerprint.path_id
                
                # 获取 stable path 信息
                fingerprint.stable_sequence = stable_sequences.get(testcase_name, [])
                fingerprint.stable_path_id = self.fingerprinter.compute_stable_hash(fingerprint.stable_sequence) if fingerprint.stable_sequence else ""
                
                if fingerprint.stable_path_id:
                    stable_path_ids.add(fingerprint.stable_path_id)
                
                # 构建 locations
                locations = []
                for pc in pcs:
                    if pc in self.resolver._lookup_table:
                        locations.extend(self.resolver._lookup_table[pc])
                loc_dicts = [loc.to_dict() for loc in locations if loc.file and loc.line > 0]
                
                # 构建 sequence
                sequence = self._build_execution_path_sequence(pcs)
                
                # 提交数据库写任务
                futures.append(
                    executor.submit(_parallel_db_write_worker, 
                                    testcase_name, path_id, 
                                    fingerprint.stable_sequence, fingerprint.stable_path_id,
                                    sequence, loc_dicts, self.config)
                )
                
            for future in tqdm(as_completed(futures), total=len(futures), desc="并发入库源码覆盖"):
                future.result()

        self.stats['unique_stable_paths'] = len(stable_path_ids)
