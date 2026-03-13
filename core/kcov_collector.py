"""
KCOV 数据采集模块
负责运行 KCOV 采集程序并收集原始 PC 序列
"""
import subprocess
import os
from pathlib import Path
from typing import List, Optional
from utils.config import Config


class KCOVCollector:
    """KCOV 数据采集器"""
    
    def __init__(self, config: Config):
        self.config = config
        self.kcov_runner = Path(config.kcov_runner_path)
        self.timeout = config.kcov_timeout
        
    def collect(self, testcase_path: str, output_file: Optional[str] = None) -> List[str]:
        """
        收集单个测试用例的 KCOV 数据
        
        Args:
            testcase_path: 测试用例路径
            output_file: 输出文件路径，如果为 None 则返回内存
            
        Returns:
            PC 序列列表
        """
        if not self.kcov_runner.exists():
            raise FileNotFoundError(f"KCOV runner not found: {self.kcov_runner}")
        
        try:
            # 运行 KCOV 采集程序
            # 注意：即使 verifier 失败（返回码非 0），也要尝试收集数据
            result = subprocess.run(
                [str(self.kcov_runner), testcase_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False  # 不自动抛出异常，让我们自己处理
            )
            
            # 从输出或文件读取 PC 序列
            if output_file:
                pcs = self._read_pcs_from_file(output_file)
            else:
                # 默认从 verifier_pcs.txt 读取
                default_file = "verifier_pcs.txt"
                if os.path.exists(default_file):
                    pcs = self._read_pcs_from_file(default_file)
                else:
                    # 从 stdout 解析
                    pcs = self._parse_pcs_from_stdout(result.stdout)
            
            # 如果没有收集到 PC，才报告错误
            if not pcs:
                if result.returncode != 0:
                    print(f"[WARNING] {testcase_path} 验证失败，且未收集到 KCOV 数据")
                else:
                    print(f"[WARNING] {testcase_path} 未收集到任何 KCOV 数据")
            
            return pcs
                    
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"KCOV collection timeout for {testcase_path}")
        except Exception as e:
            # 其他错误，打印警告但继续
            print(f"[WARNING] 收集 {testcase_path} 时出错：{e}")
            return []
    
    def collect_batch(self, testcase_paths: List[str], output_dir: str) -> dict:
        """
        批量收集多个测试用例的 KCOV 数据
        
        Args:
            testcase_paths: 测试用例路径列表
            output_dir: 输出目录
            
        Returns:
            {testcase_name: pc_list} 字典
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        results = {}
        
        for testcase in testcase_paths:
            output_file = os.path.join(output_dir, f"{Path(testcase).stem}_pcs.txt")
            try:
                pcs = self.collect(testcase, output_file)
                results[testcase] = pcs
            except Exception as e:
                print(f"[!] Failed to collect {testcase}: {e}")
                results[testcase] = []
        
        return results
    
    def _read_pcs_from_file(self, file_path: str) -> List[str]:
        """从文件读取 PC 序列"""
        with open(file_path, 'r') as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    
    def _parse_pcs_from_stdout(self, stdout: str) -> List[str]:
        """从标准输出解析 PC 序列"""
        pcs = []
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith('0x'):
                pcs.append(line)
        return pcs
    
    def validate_kcov_runner(self) -> bool:
        """验证 KCOV runner 是否可用"""
        try:
            result = subprocess.run(
                [str(self.kcov_runner), "--help"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False
