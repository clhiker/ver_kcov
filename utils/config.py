"""
配置管理模块
"""
import yaml
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class Config:
    """配置类"""
    # vmlinux 路径
    vmlinux_path: str = "./vmlinux"
    
    # KCOV runner 路径
    kcov_runner_path: str = "./kcov_runner"
    
    # KCOV 超时时间（秒）
    kcov_timeout: int = 30
    
    # Verifier 地址范围
    verifier_start_addr: int = 0xffffffff811a4500
    verifier_end_addr: int = 0xffffffff811b2000
    
    # 测试用例目录
    testcase_dir: str = "./testcases"
    
    # 结果输出目录
    result_dir: str = "./path_results"
    
    # 数据库路径
    db_path: str = "./kcov_coverage.db"
    
    # 缓存文件路径
    lookup_table_cache: str = "./cache/pc_lookup_table.txt"
    
    # 日志级别
    log_level: str = "INFO"
    
    # 并行工作进程数
    parallel_workers: int = 4
    
    # 是否使用 llvm-symbolizer（默认 True，使用 llvm-symbolizer）
    use_llvm_symbolizer: bool = True
    
    @classmethod
    def from_yaml(cls, config_path: str) -> 'Config':
        """从 YAML 文件加载配置"""
        config = cls()
        
        if Path(config_path).exists():
            # 获取配置文件所在目录，用于解析相对路径
            config_dir = Path(config_path).resolve().parent
            
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f)
                
                if 'vmlinux_path' in data:
                    path = data['vmlinux_path']
                    config.vmlinux_path = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'kcov_runner_path' in data:
                    path = data['kcov_runner_path']
                    config.kcov_runner_path = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'kcov_timeout' in data:
                    config.kcov_timeout = data['kcov_timeout']
                if 'verifier_start_addr' in data:
                    config.verifier_start_addr = int(data['verifier_start_addr'], 16) if isinstance(data['verifier_start_addr'], str) else data['verifier_start_addr']
                if 'verifier_end_addr' in data:
                    config.verifier_end_addr = int(data['verifier_end_addr'], 16) if isinstance(data['verifier_end_addr'], str) else data['verifier_end_addr']
                if 'testcase_dir' in data:
                    path = data['testcase_dir']
                    config.testcase_dir = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'result_dir' in data:
                    path = data['result_dir']
                    config.result_dir = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'db_path' in data:
                    path = data['db_path']
                    config.db_path = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'lookup_table_cache' in data:
                    path = data['lookup_table_cache']
                    config.lookup_table_cache = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'log_level' in data:
                    config.log_level = data['log_level']
                if 'parallel_workers' in data:
                    config.parallel_workers = data['parallel_workers']
                if 'use_llvm_symbolizer' in data:
                    config.use_llvm_symbolizer = data['use_llvm_symbolizer']
        
        return config
    
    def to_yaml(self, config_path: str):
        """保存配置到 YAML 文件"""
        data = {
            'vmlinux_path': self.vmlinux_path,
            'kcov_runner_path': self.kcov_runner_path,
            'kcov_timeout': self.kcov_timeout,
            'verifier_start_addr': hex(self.verifier_start_addr),
            'verifier_end_addr': hex(self.verifier_end_addr),
            'testcase_dir': self.testcase_dir,
            'result_dir': self.result_dir,
            'db_path': self.db_path,
            'lookup_table_cache': self.lookup_table_cache,
            'log_level': self.log_level,
            'parallel_workers': self.parallel_workers,
            'use_llvm_symbolizer': self.use_llvm_symbolizer
        }
        
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)
    
    def validate(self) -> bool:
        """验证配置是否有效"""
        # 检查必要文件是否存在
        if not Path(self.vmlinux_path).exists():
            print(f"[!] vmlinux not found: {self.vmlinux_path}")
            return False
        
        if not Path(self.kcov_runner_path).exists():
            print(f"[!] KCOV runner not found: {self.kcov_runner_path}")
            return False
        
        # 检查地址范围
        if self.verifier_start_addr >= self.verifier_end_addr:
            print("[!] Invalid verifier address range")
            return False
        
        return True
