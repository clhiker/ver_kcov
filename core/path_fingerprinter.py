"""
路径指纹生成模块
负责过滤、去重、生成路径哈希指纹
"""
import hashlib
from typing import List, Tuple, Set, Dict
from dataclasses import dataclass
from utils.config import Config


@dataclass
class PathFingerprint:
    """路径指纹数据结构"""
    path_id: str  # SHA-256 哈希前 16 位
    pcs: List[str]  # 过滤后的 PC 序列
    pc_count: int  # PC 数量
    raw_count: int  # 原始 PC 数量
    compression_rate: float  # 压缩率
    
    def to_dict(self) -> dict:
        return {
            'path_id': self.path_id,
            'pcs': self.pcs,
            'pc_count': self.pc_count,
            'raw_count': self.raw_count,
            'compression_rate': self.compression_rate
        }


class PathFingerprinter:
    """路径指纹生成器"""
    
    def __init__(self, config: Config):
        self.config = config
        self.verifier_start = config.verifier_start_addr
        self.verifier_end = config.verifier_end_addr
        
    def generate(self, raw_pcs: List[str]) -> PathFingerprint:
        """
        从原始 PC 序列生成路径指纹
        
        步骤：
        1. 过滤：只保留 verifier.c 地址范围内的 PC
        2. 折叠：连续重复的 PC 只保留一个
        3. 哈希：生成 SHA-256 指纹
        
        Args:
            raw_pcs: 原始 PC 序列（字符串或整数列表）
            
        Returns:
            PathFingerprint 对象
        """
        raw_count = len(raw_pcs)
        
        # 1. 地址过滤 + 2. 连续去重
        filtered = self._filter_and_fold(raw_pcs)
        
        # 3. 生成哈希
        path_id = self._compute_hash(filtered)
        
        # 计算压缩率
        compression_rate = len(filtered) / raw_count if raw_count > 0 else 0.0
        
        return PathFingerprint(
            path_id=path_id,
            pcs=filtered,
            pc_count=len(filtered),
            raw_count=raw_count,
            compression_rate=compression_rate
        )
    
    def _filter_and_fold(self, raw_pcs: List[str]) -> List[str]:
        """过滤地址并连续去重"""
        filtered = []
        last_pc = None
        
        for pc in raw_pcs:
            # 转换为整数进行比较
            pc_int = int(pc, 16) if isinstance(pc, str) else pc
            
            # 地址范围过滤
            if self.verifier_start <= pc_int <= self.verifier_end:
                # 连续去重（Fold）
                pc_hex = f"0x{pc_int:x}" if isinstance(pc, int) else pc
                if pc_hex != last_pc:
                    filtered.append(pc_hex)
                    last_pc = pc_hex
        
        return filtered
    
    def _compute_hash(self, pcs: List[str]) -> str:
        """计算路径哈希"""
        path_str = ",".join(pcs)
        full_hash = hashlib.sha256(path_str.encode()).hexdigest()
        return full_hash[:16]  # 取前 16 位作为 ID
    
    def generate_batch(self, all_pcs: Dict[str, List[str]]) -> Dict[str, PathFingerprint]:
        """
        批量生成路径指纹
        
        Args:
            all_pcs: {testcase_name: pc_list}
            
        Returns:
            {testcase_name: PathFingerprint}
        """
        results = {}
        for testcase_name, pcs in all_pcs.items():
            results[testcase_name] = self.generate(pcs)
        return results
    
    def get_unique_paths(self, fingerprints: Dict[str, PathFingerprint]) -> Dict[str, List[str]]:
        """
        提取所有唯一路径
        
        Args:
            fingerprints: 测试用例到指纹的映射
            
        Returns:
            {path_id: pc_list} 唯一路径字典
        """
        unique_paths = {}
        for testcase, fp in fingerprints.items():
            if fp.path_id not in unique_paths:
                unique_paths[fp.path_id] = fp.pcs
        return unique_paths
    
    def is_verifier_address(self, pc: str) -> bool:
        """检查地址是否在 verifier 范围内"""
        pc_int = int(pc, 16) if isinstance(pc, str) else pc
        return self.verifier_start <= pc_int <= self.verifier_end
    
    def update_address_range(self, start: int, end: int):
        """更新 verifier 地址范围"""
        self.verifier_start = start
        self.verifier_end = end
