"""
Verifier 覆盖率采集框架核心模块
"""
from .kcov_collector import KCOVCollector
from .path_fingerprinter import PathFingerprinter, PathFingerprint
from .pc_resolver import PCResolver, SourceLocation
from .coverage_db import CoverageDatabase

__all__ = [
    'KCOVCollector',
    'PathFingerprinter',
    'PathFingerprint',
    'PCResolver',
    'SourceLocation',
    'CoverageDatabase'
]
