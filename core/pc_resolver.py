"""
全局 PC 地址解析模块
批量将 PC 地址映射到源码行号
"""
import subprocess
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass
from utils.config import Config


@dataclass
class SourceLocation:
    """源码位置信息"""
    file: str
    line: int
    function: str
    address: str
    
    def to_dict(self) -> dict:
        return {
            'file': self.file,
            'line': self.line,
            'function': self.function,
            'address': self.address
        }


class PCResolver:
    """PC 地址解析器"""
    
    def __init__(self, config: Config):
        self.config = config
        self.vmlinux_path = config.vmlinux_path
        self._lookup_table: Dict[str, SourceLocation] = {}
        
    def build_lookup_table(self, unique_pcs: Set[str], cache_file: Optional[str] = None) -> Dict[str, SourceLocation]:
        """
        构建 PC 到源码行号的查找表
        
        优化策略：
        1. 收集所有唯一 PC 地址
        2. 一次性批量运行 addr2line
        3. 建立 O(1) 查找表
        
        Args:
            unique_pcs: 所有唯一 PC 地址集合
            cache_file: 缓存文件路径
            
        Returns:
            {pc_address: SourceLocation} 查找表
        """
        if cache_file and os.path.exists(cache_file):
            return self._load_lookup_table(cache_file)
        
        # 准备 addr2line 输入
        pc_list = sorted(list(unique_pcs))
        
        # 批量运行 addr2line
        lookup_table = self._run_batch_addr2line(pc_list)
        
        # 保存到缓存
        if cache_file:
            self._save_lookup_table(lookup_table, cache_file)
        
        self._lookup_table = lookup_table
        return lookup_table
    
    def _run_batch_addr2line(self, pcs: List[str]) -> Dict[str, SourceLocation]:
        """批量运行 addr2line"""
        if not pcs:
            return {}
        
        # 准备输入内容
        input_text = "\n".join(pcs)
        
        try:
            # 运行 addr2line
            # -e: 指定可执行文件
            # -f: 显示函数名
            # -i: 显示 inline 信息
            # -p: 简洁输出格式
            result = subprocess.run(
                ['addr2line', '-e', self.vmlinux_path, '-f', '-i', '-p'],
                input=input_text,
                capture_output=True,
                text=True,
                check=True
            )
            
            # 解析输出
            lookup_table = {}
            output_lines = result.stdout.strip().split('\n')
            
            for pc, output_line in zip(pcs, output_lines):
                location = self._parse_addr2line_output(pc, output_line)
                if location:
                    lookup_table[pc] = location
            
            return lookup_table
            
        except subprocess.CalledProcessError as e:
            print(f"[!] addr2line failed: {e.stderr}")
            return {}
        except FileNotFoundError:
            print("[!] addr2line not found, please install binutils")
            return {}
    
    def _parse_addr2line_output(self, pc: str, output: str) -> Optional[SourceLocation]:
        """
        解析 addr2line 输出
        
        格式：function_name file:line 或 function_name ??:?
        """
        if not output or output == "?? ??:0":
            return None
        
        try:
            # 解析格式："func_name file:line" 或 "func_name file:line (inline)"
            parts = output.split(' ', 1)
            if len(parts) != 2:
                return None
            
            func_name = parts[0]
            location_part = parts[1]
            
            # 处理 inline 信息
            if '(inline)' in location_part:
                location_part = location_part.replace('(inline)', '').strip()
            
            # 解析 file:line
            if ':' in location_part:
                file_path, line_str = location_part.rsplit(':', 1)
                line_num = int(line_str) if line_str.isdigit() else 0
            else:
                file_path = location_part
                line_num = 0
            
            return SourceLocation(
                file=file_path,
                line=line_num,
                function=func_name,
                address=pc
            )
        except Exception as e:
            print(f"[!] Failed to parse addr2line output '{output}': {e}")
            return None
    
    def _save_lookup_table(self, lookup_table: Dict[str, SourceLocation], cache_file: str):
        """保存查找表到文件"""
        Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
        
        with open(cache_file, 'w') as f:
            for pc, loc in lookup_table.items():
                f.write(f"{pc}|{loc.function}|{loc.file}|{loc.line}\n")
    
    def _load_lookup_table(self, cache_file: str) -> Dict[str, SourceLocation]:
        """从缓存文件加载查找表"""
        lookup_table = {}
        
        with open(cache_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                parts = line.split('|')
                if len(parts) != 4:
                    continue
                
                pc, func, file_path, line_num = parts
                lookup_table[pc] = SourceLocation(
                    file=file_path,
                    line=int(line_num),
                    function=func,
                    address=pc
                )
        
        return lookup_table
    
    def resolve_path(self, pcs: List[str]) -> List[SourceLocation]:
        """
        解析整个路径的源码位置
        
        Args:
            pcs: PC 序列
            
        Returns:
            SourceLocation 列表
        """
        locations = []
        for pc in pcs:
            if pc in self._lookup_table:
                locations.append(self._lookup_table[pc])
            else:
                # 如果不在查找表中，单独解析
                loc = self._resolve_single_pc(pc)
                if loc:
                    locations.append(loc)
        return locations
    
    def _resolve_single_pc(self, pc: str) -> Optional[SourceLocation]:
        """单独解析一个 PC 地址"""
        try:
            result = subprocess.run(
                ['addr2line', '-e', self.vmlinux_path, '-f', '-i', '-p', pc],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                return self._parse_addr2line_output(pc, result.stdout.strip())
        except Exception as e:
            print(f"[!] Failed to resolve {pc}: {e}")
        
        return None
    
    def get_covered_lines(self, pcs: List[str]) -> Set[Tuple[str, int]]:
        """
        获取路径覆盖的所有源码行
        
        Returns:
            {(file, line)} 集合
        """
        covered = set()
        locations = self.resolve_path(pcs)
        
        for loc in locations:
            if loc.file and loc.line > 0:
                covered.add((loc.file, loc.line))
        
        return covered
    
    def update_vmlinux_path(self, path: str):
        """更新 vmlinux 路径"""
        self.vmlinux_path = path
        self._lookup_table = {}  # 清空缓存
