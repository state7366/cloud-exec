# -*- coding: utf-8 -*-
"""exec.py —— 实例端 Python 脚本执行(本地写 .py → sftp → conda run python，零转义)。

根治 heredoc / `python -c repr(script)` 的转义坑：统一「本地写临时 .py → sftp 上传 →
conda run -n <env> python -u <file>」。
"""
from __future__ import annotations

import os
import tempfile
from typing import Optional

from .ssh import SshClient


class PyExecClient:
    """远程 python 执行。conda_root/default_cwd 缺省为 DockBuddy 实例端约定，可覆盖。"""

    def __init__(self, ssh: Optional[SshClient] = None,
                 conda_root: str = "/home/ubuntu/miniconda3",
                 default_cwd: str = "/home/ubuntu"):
        self.ssh = ssh or SshClient()
        self.conda_root = conda_root
        self.default_cwd = default_cwd

    def run_py(self, ip: str, py_code: str, env: str = "dockbuddy", args: str = "",
               cwd: str = "", timeout: int = 3600) -> tuple:
        """本地写 .py → sftp 上传 → conda run -n env python -u 执行, 返回 (rc, out, err)。"""
        cwd = (cwd or self.default_cwd).rstrip("/")
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8", newline="\n") as f:
            f.write(py_code)
            tmp = f.name
        remote = cwd + "/" + os.path.basename(tmp)  # POSIX 拼接(禁用 os.path.join)
        try:
            self.ssh.sftp_put(ip, tmp, remote)
            conda = f"{self.conda_root}/bin/conda"
            cmd = f"cd {cwd} && {conda} run -n {env} python -u {remote} {args}"
            return self.ssh.ssh_run(ip, cmd, timeout=timeout, echo=False)
        finally:
            os.remove(tmp)

    def run_py_bg(self, ip: str, py_code: str, env: str = "dockbuddy", args: str = "",
                  cwd: str = "", log_path: str = "/home/ubuntu/py_exec.log",
                  done_marker: Optional[str] = None, timeout: int = 7200,
                  on_progress=None) -> bool:
        """后台跑 python 脚本 + 增量日志实时打印, 返回是否完成。"""
        cwd = (cwd or self.default_cwd).rstrip("/")
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8", newline="\n") as f:
            f.write(py_code)
            tmp = f.name
        remote = cwd + "/" + os.path.basename(tmp)
        try:
            self.ssh.sftp_put(ip, tmp, remote)
            conda = f"{self.conda_root}/bin/conda"
            cmd = f"cd {cwd} && {conda} run -n {env} python -u {remote} {args}"
            self.ssh.ssh_run_bg(ip, cmd, log_path, done_marker=done_marker)
            return self.ssh.stream_log(ip, log_path,
                                       done_marker=done_marker or log_path + ".done",
                                       timeout=timeout)
        finally:
            os.remove(tmp)


__all__ = ["PyExecClient"]
