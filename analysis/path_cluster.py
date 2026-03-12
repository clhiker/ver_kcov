"""
路径聚类分析模块
根据路径相似度对测试用例进行聚类
"""
from typing import List, Dict, Set, Tuple
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class PathCluster:
    """路径聚类"""
    cluster_id: int
    path_hashes: List[str]
    common_prefix_length: int
    description: str = ""


class PathClusterer:
    """路径聚类分析器"""
    
    def __init__(self):
        self.clusters: List[PathCluster] = []
    
    def cluster_by_prefix(self, all_paths: Dict[str, List[str]], 
                         min_prefix_length: int = 5) -> List[PathCluster]:
        """
        根据路径前缀相似度进行聚类
        
        Args:
            all_paths: {path_hash: pc_list}
            min_prefix_length: 最小公共前缀长度
            
        Returns:
            聚类列表
        """
        # 提取所有路径的前缀
        prefix_map = defaultdict(list)
        
        for path_hash, pcs in all_paths.items():
            if len(pcs) >= min_prefix_length:
                prefix = tuple(pcs[:min_prefix_length])
                prefix_map[prefix].append(path_hash)
        
        # 创建聚类
        clusters = []
        cluster_id = 0
        
        for prefix, path_hashes in prefix_map.items():
            if len(path_hashes) >= 1:  # 至少有一个路径
                cluster = PathCluster(
                    cluster_id=cluster_id,
                    path_hashes=path_hashes,
                    common_prefix_length=min_prefix_length,
                    description=f"共享前 {min_prefix_length} 个 PC 的路径组"
                )
                clusters.append(cluster)
                cluster_id += 1
        
        self.clusters = clusters
        return clusters
    
    def cluster_by_function(self, path_locations: Dict[str, List[Tuple[str, int, str]]]) -> Dict[str, List[str]]:
        """
        根据访问的函数进行聚类
        
        Args:
            path_locations: {path_hash: [(file, line, function)]}
            
        Returns:
            {function_name: [path_hashes]}
        """
        function_paths = defaultdict(list)
        
        for path_hash, locations in path_locations.items():
            # 提取该路径访问的所有函数
            functions = set(loc[2] for loc in locations if loc[2])
            
            # 将路径添加到每个访问过的函数
            for func in functions:
                function_paths[func].append(path_hash)
        
        return dict(function_paths)
    
    def find_divergence_point(self, path1: List[str], path2: List[str]) -> int:
        """
        找到两条路径的分叉点
        
        Returns:
            分叉点的索引位置，如果完全相同返回 -1
        """
        min_len = min(len(path1), len(path2))
        
        for i in range(min_len):
            if path1[i] != path2[i]:
                return i
        
        if len(path1) != len(path2):
            return min_len
        
        return -1  # 完全相同
    
    def compute_path_similarity(self, path1: List[str], path2: List[str]) -> float:
        """
        计算两条路径的相似度（Jaccard 相似度）
        
        Returns:
            相似度分数 [0, 1]
        """
        set1 = set(path1)
        set2 = set(path2)
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        
        return intersection / union if union > 0 else 0.0
    
    def analyze_cluster_diversity(self, all_paths: Dict[str, List[str]]) -> dict:
        """
        分析聚类多样性
        
        Returns:
            多样性统计
        """
        if not self.clusters:
            self.cluster_by_prefix(all_paths)
        
        stats = {
            'total_clusters': len(self.clusters),
            'cluster_sizes': [],
            'avg_cluster_size': 0,
            'largest_cluster': 0,
            'singleton_clusters': 0
        }
        
        sizes = [len(c.path_hashes) for c in self.clusters]
        stats['cluster_sizes'] = sizes
        stats['avg_cluster_size'] = sum(sizes) / len(sizes) if sizes else 0
        stats['largest_cluster'] = max(sizes) if sizes else 0
        stats['singleton_clusters'] = sum(1 for s in sizes if s == 1)
        
        return stats
    
    def get_cluster_summary(self) -> List[dict]:
        """获取聚类摘要"""
        return [
            {
                'cluster_id': c.cluster_id,
                'path_count': len(c.path_hashes),
                'description': c.description
            }
            for c in self.clusters
        ]
