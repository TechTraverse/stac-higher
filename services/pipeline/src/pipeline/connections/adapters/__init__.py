"""Protocol StorageAdapters + the adapter_for factory and TOFU logic."""

from pipeline.connections.adapters.base import FileEntry, StorageAdapter, TestResult
from pipeline.connections.adapters.factory import adapter_for
from pipeline.connections.adapters.ftp import FtpAdapter
from pipeline.connections.adapters.ftps import FtpsAdapter
from pipeline.connections.adapters.s3 import S3Adapter
from pipeline.connections.adapters.sftp import SftpAdapter
from pipeline.connections.adapters.tofu import (
    TofuDecision,
    TofuVerdict,
    evaluate_host_key,
)

__all__ = [
    "FileEntry",
    "FtpAdapter",
    "FtpsAdapter",
    "S3Adapter",
    "SftpAdapter",
    "StorageAdapter",
    "TestResult",
    "TofuDecision",
    "TofuVerdict",
    "adapter_for",
    "evaluate_host_key",
]
