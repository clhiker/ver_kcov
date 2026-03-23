"""
全局 PC 地址解析模块
批量将 PC 地址映射到源码行号
"""
import json
import subprocess
import os
import shutil
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

    CACHE_VERSION = "v4-json-frames-vmlinux-bound"
    
    def __init__(self, config: Config):
        self.config = config
        self.vmlinux_path = config.vmlinux_path
        self._lookup_table: Dict[str, List[SourceLocation]] = {}
        self.use_llvm = True
        self.llvm_symbolizer = self._find_llvm_symbolizer()

    def _find_llvm_symbolizer(self) -> str:
        """优先使用仓库内置的 llvm-symbolizer，其次回退到 PATH。"""
        candidates = [
            Path(self.config.vmlinux_path).resolve().parent / "llvm-symbolizer",
            Path(__file__).resolve().parents[1] / "llvm-symbolizer",
        ]
        for candidate in candidates:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)

        system_binary = shutil.which("llvm-symbolizer")
        return system_binary or "llvm-symbolizer"
        
    def build_lookup_table(self, unique_pcs: Set[str], cache_file: Optional[str] = None) -> Dict[str, List[SourceLocation]]:
        """
        构建 PC 到源码行号的查找表
        
        优化策略：
        1. 收集所有唯一 PC 地址
        2. 一次性批量运行 llvm-symbolizer
        3. 建立 O(1) 查找表
        
        Args:
            unique_pcs: 所有唯一 PC 地址集合
            cache_file: 缓存文件路径
            
        Returns:
            {pc_address: [SourceLocation, ...]} 查找表
        """
        if cache_file and os.path.exists(cache_file):
            self._lookup_table = self._load_lookup_table(cache_file)
            return self._lookup_table
        
        # 准备 llvm-symbolizer 输入
        pc_list = sorted(list(unique_pcs))
        
        # 批量运行 llvm-symbolizer
        lookup_table = self._run_batch_llvm_symbolizer(pc_list)
        
        # 保存到缓存
        if cache_file:
            self._save_lookup_table(lookup_table, cache_file)
        
        self._lookup_table = lookup_table
        return lookup_table
    
    def _run_batch_llvm_symbolizer(self, pcs: List[str]) -> Dict[str, List[SourceLocation]]:
        """批量运行 llvm-symbolizer，分批处理避免超时"""
        if not pcs:
            return {}
        
        # 分批处理，每批 10000 个地址
        BATCH_SIZE = 10000
        lookup_table = {}
        total = len(pcs)
        
        print(f"[*] 开始解析 {total} 个 PC 地址（分批处理，每批{BATCH_SIZE}个）...")
        
        for i in range(0, total, BATCH_SIZE):
            batch = pcs[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
            
            print(f"\r[*] 处理批次 {batch_num}/{total_batches}...", end='', flush=True)
            
            # 准备输入内容
            input_text = "\n".join(batch)
            
            try:
                result = subprocess.run(
                    [self.llvm_symbolizer, '-e', self.vmlinux_path, '--functions', '--inlining', '--demangle', '--output-style=JSON'],
                    input=input_text,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=300
                )
                batch_results = self._parse_llvm_output(result.stdout)
                
                lookup_table.update(batch_results)
                
            except subprocess.TimeoutExpired:
                print(f"\n[!] 批次 {batch_num} 超时，跳过...")
                continue
            except subprocess.CalledProcessError as e:
                print(f"\n[!] 批次 {batch_num} 失败：{e.stderr}")
                continue
            except FileNotFoundError:
                print(f"\n[!] llvm-symbolizer 未找到")
                return {}
        
        print(f"\r[*] PC 地址解析完成，共解析 {len(lookup_table)} 个地址")
        return lookup_table
    
    def _parse_llvm_output(self, output: str) -> Dict[str, List[SourceLocation]]:
        """
        解析 llvm-symbolizer 输出

        使用 JSON 输出格式，避免 GNU 风格输出在 inline frame
        场景下导致的行级错位解析。
        """
        lookup_table = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"\n[!] 解析 llvm JSON 输出失败：{e}")
                continue

            pc = record.get('Address', '')
            symbols = record.get('Symbol') or []
            if not pc or not symbols:
                continue

            locations = []
            for frame in symbols:
                file_path = frame.get('FileName', '')
                line_num = frame.get('Line', 0) or 0
                func_name = frame.get('FunctionName', '')

                if file_path and line_num:
                    locations.append(SourceLocation(
                        file=file_path,
                        line=line_num,
                        function=func_name,
                        address=pc
                    ))

            if locations:
                lookup_table[pc] = locations
        
        return lookup_table
    
    def _save_lookup_table(self, lookup_table: Dict[str, List[SourceLocation]], cache_file: str):
        """保存查找表到文件"""
        Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
        vmlinux = Path(self.vmlinux_path)
        stat = vmlinux.stat()
        header = (
            f"# pc_resolver_cache {self.CACHE_VERSION} "
            f"{vmlinux.resolve()} {stat.st_mtime_ns} {stat.st_size}"
        )
        
        with open(cache_file, 'w') as f:
            f.write(f"{header}\n")
            for pc, locations in lookup_table.items():
                payload = [loc.to_dict() for loc in locations]
                f.write(f"{pc}|{json.dumps(payload, ensure_ascii=True)}\n")
    
    def _load_lookup_table(self, cache_file: str) -> Dict[str, List[SourceLocation]]:
        """从缓存文件加载查找表"""
        lookup_table = {}
        vmlinux = Path(self.vmlinux_path)
        stat = vmlinux.stat()
        expected_header = (
            f"# pc_resolver_cache {self.CACHE_VERSION} "
            f"{vmlinux.resolve()} {stat.st_mtime_ns} {stat.st_size}"
        )
        
        with open(cache_file, 'r') as f:
            header = f.readline().strip()
            if header != expected_header:
                return {}

            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                parts = line.split('|', 1)
                if len(parts) != 2:
                    continue
                
                pc, payload = parts
                try:
                    frames = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                locations = []
                for frame in frames:
                    locations.append(SourceLocation(
                        file=frame['file'],
                        line=int(frame['line']),
                        function=frame.get('function', ''),
                        address=frame.get('address', pc)
                    ))

                if locations:
                    lookup_table[pc] = locations
        
        return lookup_table
    
    def resolve_path(self, pcs: List[str]) -> List[SourceLocation]:
        """
        解析整个路径的源码位置，带进度显示
        
        Args:
            pcs: PC 序列
            
        Returns:
            SourceLocation 列表
        """
        locations = []
        total = len(pcs)
        
        for i, pc in enumerate(pcs, 1):
            if pc in self._lookup_table:
                locations.extend(self._lookup_table[pc])
            else:
                # 如果不在查找表中，单独解析
                if i % 10 == 0 or i == total:  # 每 10 个显示一次进度
                    print(f"\r[*] 解析 PC {i}/{total}...", end='', flush=True)
                resolved = self._resolve_single_pc(pc)
                if resolved:
                    locations.extend(resolved)
        
        if total > 0:
            print(f"\r[*] 路径解析完成，共 {len(locations)} 个位置")
        
        return locations
    
    def _resolve_single_pc(self, pc: str) -> Optional[List[SourceLocation]]:
        """单独解析一个 PC 地址"""
        try:
            result = subprocess.run(
                [self.llvm_symbolizer, '-e', self.vmlinux_path, '--functions', '--inlining', '--demangle', '--output-style=JSON'],
                input=pc,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                lookup_table = self._parse_llvm_output(result.stdout)
                return lookup_table.get(pc)
        except subprocess.TimeoutExpired:
            print(f"\n[!] llvm-symbolizer 超时：{pc}")
        except Exception as e:
            print(f"\n[!] 解析失败 {pc}: {e}")
        
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
