"""
配置管理模块
"""
import yaml
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "kcov_config.yaml"


@dataclass
class Config:
    """配置类"""
    # vmlinux 路径
    vmlinux_path: str = "./vmlinux"
    
    # KCOV runner 路径
    kcov_runner_path: str = "./kcov_runner"

    # Runner 二进制名称或路径（可选，优先级高于 kcov_runner_path）
    runner_binary: str = ""
    
    # KCOV 超时时间（秒）
    kcov_timeout: int = 30
    
    # Verifier 地址范围
    verifier_start_addr: int = 0xffffffff811a4500
    verifier_end_addr: int = 0xffffffff811b2000
    
    # 测试用例目录
    testcase_dir: str = "./testcases"

    # Agent seed 反查目录配置
    agent_source_code_dir: str = ""
    agent_bytecode_dir: str = ""
    
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

    # 稳定路径的源码行分桶大小
    stable_path_line_bucket: int = 64
    
    use_llvm_symbolizer: bool = True

    # Agent / campaign / local model settings
    agent_provider: str = ""
    agent_model: str = ""
    agent_temperature: float = 0.7
    agent_objective: str = "enrich_sparse"
    agent_max_iterations: int = 3
    agent_top_k: int = 5
    agent_nearby_budget: int = 3
    agent_campaign_output_root: str = "./mutated-cases/campaign"
    agent_campaign_sleep_seconds: int = 10
    ollama_host: str = "http://127.0.0.1:11434"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    google_api_key: str = ""

    # VM settings
    vm_ssh_key: str = ""
    vm_ssh_port: int = 0
    vm_ssh_host: str = ""
    vm_guest_mount_point: str = ""
    
    @classmethod
    def from_yaml(cls, config_path: str) -> 'Config':
        """从 YAML 文件加载配置"""
        config = cls()
        
        if Path(config_path).exists():
            # 获取配置文件所在目录，用于解析相对路径
            config_dir = Path(config_path).resolve().parent
            
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f) or {}
                
                if 'vmlinux_path' in data:
                    path = data['vmlinux_path']
                    config.vmlinux_path = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'kcov_runner_path' in data:
                    path = data['kcov_runner_path']
                    config.kcov_runner_path = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'runner_binary' in data:
                    runner_binary = str(data['runner_binary']).strip()
                    config.runner_binary = runner_binary
                    if runner_binary:
                        runner_path = Path(runner_binary)
                        if runner_path.is_absolute():
                            config.kcov_runner_path = str(runner_path)
                        elif runner_path.parent == Path('.'):
                            config.kcov_runner_path = str((config_dir.parent / runner_path).resolve())
                        else:
                            config.kcov_runner_path = str((config_dir / runner_path).resolve())
                if 'kcov_timeout' in data:
                    config.kcov_timeout = data['kcov_timeout']
                if 'verifier_start_addr' in data:
                    config.verifier_start_addr = int(data['verifier_start_addr'], 16) if isinstance(data['verifier_start_addr'], str) else data['verifier_start_addr']
                if 'verifier_end_addr' in data:
                    config.verifier_end_addr = int(data['verifier_end_addr'], 16) if isinstance(data['verifier_end_addr'], str) else data['verifier_end_addr']
                if 'testcase_dir' in data:
                    path = data['testcase_dir']
                    config.testcase_dir = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'agent_source_code_dir' in data:
                    path = data['agent_source_code_dir']
                    config.agent_source_code_dir = str(config_dir / path) if not Path(path).is_absolute() else path
                elif 'agent_source_code_dirs' in data and data['agent_source_code_dirs']:
                    path = data['agent_source_code_dirs'][0]
                    config.agent_source_code_dir = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'agent_bytecode_dir' in data:
                    path = data['agent_bytecode_dir']
                    config.agent_bytecode_dir = str(config_dir / path) if not Path(path).is_absolute() else path
                elif 'agent_bytecode_dirs' in data and data['agent_bytecode_dirs']:
                    path = data['agent_bytecode_dirs'][0]
                    config.agent_bytecode_dir = str(config_dir / path) if not Path(path).is_absolute() else path
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
                if 'stable_path_line_bucket' in data:
                    config.stable_path_line_bucket = data['stable_path_line_bucket']
                if 'use_llvm_symbolizer' in data:
                    config.use_llvm_symbolizer = data['use_llvm_symbolizer']
                if 'agent_provider' in data:
                    config.agent_provider = data['agent_provider']
                if 'agent_model' in data:
                    config.agent_model = data['agent_model']
                if 'agent_temperature' in data:
                    config.agent_temperature = data['agent_temperature']
                if 'agent_objective' in data:
                    config.agent_objective = data['agent_objective']
                if 'agent_max_iterations' in data:
                    config.agent_max_iterations = data['agent_max_iterations']
                if 'agent_top_k' in data:
                    config.agent_top_k = data['agent_top_k']
                if 'agent_nearby_budget' in data:
                    config.agent_nearby_budget = data['agent_nearby_budget']
                if 'agent_campaign_output_root' in data:
                    path = data['agent_campaign_output_root']
                    config.agent_campaign_output_root = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'agent_campaign_sleep_seconds' in data:
                    config.agent_campaign_sleep_seconds = data['agent_campaign_sleep_seconds']
                if 'ollama_host' in data:
                    config.ollama_host = data['ollama_host']
                if 'openai_base_url' in data:
                    config.openai_base_url = data['openai_base_url']
                if 'openai_api_key' in data:
                    config.openai_api_key = data['openai_api_key']
                if 'openai_model' in data:
                    config.openai_model = data['openai_model']
                if 'google_api_key' in data:
                    config.google_api_key = data['google_api_key']
                if 'vm_ssh_key' in data:
                    path = data['vm_ssh_key']
                    config.vm_ssh_key = str(config_dir / path) if not Path(path).is_absolute() else path
                if 'vm_ssh_port' in data:
                    config.vm_ssh_port = data['vm_ssh_port']
                if 'vm_ssh_host' in data:
                    config.vm_ssh_host = data['vm_ssh_host']
                if 'vm_guest_mount_point' in data:
                    config.vm_guest_mount_point = data['vm_guest_mount_point']
        
        return config
    
    def to_yaml(self, config_path: str):
        """保存配置到 YAML 文件"""
        data = {
            'vmlinux_path': self.vmlinux_path,
            'kcov_timeout': self.kcov_timeout,
            'verifier_start_addr': hex(self.verifier_start_addr),
            'verifier_end_addr': hex(self.verifier_end_addr),
            'testcase_dir': self.testcase_dir,
            'agent_source_code_dir': self.agent_source_code_dir,
            'agent_bytecode_dir': self.agent_bytecode_dir,
            'result_dir': self.result_dir,
            'db_path': self.db_path,
            'lookup_table_cache': self.lookup_table_cache,
            'log_level': self.log_level,
            'parallel_workers': self.parallel_workers,
            'stable_path_line_bucket': self.stable_path_line_bucket,
            'use_llvm_symbolizer': self.use_llvm_symbolizer,
            'agent_provider': self.agent_provider,
            'agent_model': self.agent_model,
            'agent_temperature': self.agent_temperature,
            'agent_objective': self.agent_objective,
            'agent_max_iterations': self.agent_max_iterations,
            'agent_top_k': self.agent_top_k,
            'agent_nearby_budget': self.agent_nearby_budget,
            'agent_campaign_output_root': self.agent_campaign_output_root,
            'agent_campaign_sleep_seconds': self.agent_campaign_sleep_seconds,
            'ollama_host': self.ollama_host,
            'openai_base_url': self.openai_base_url,
            'openai_api_key': self.openai_api_key,
            'openai_model': self.openai_model,
            'google_api_key': self.google_api_key,
            'vm_ssh_key': self.vm_ssh_key,
            'vm_ssh_port': self.vm_ssh_port,
            'vm_ssh_host': self.vm_ssh_host,
            'vm_guest_mount_point': self.vm_guest_mount_point
        }

        # runner_binary 与 kcov_runner_path 语义重叠，优先只写 runner_binary。
        if self.runner_binary:
            data['runner_binary'] = self.runner_binary
        else:
            data['kcov_runner_path'] = self.kcov_runner_path
        
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


def load_project_config(config_path: Optional[str] = None) -> Config:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Project config not found: {path}")
    return Config.from_yaml(str(path))
