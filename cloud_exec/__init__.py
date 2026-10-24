# -*- coding: utf-8 -*-
"""cloud_exec —— 腾讯云执行层封装(CVM/TAT/COS/CBS/SSH)。

核心纯标准库(零第三方依赖), 与 DockBuddy 项目完全解耦。SSH(paramiko)与
COS multipart(qcloud_cos)为可选依赖, 懒加载 —— n8n 沙箱等无第三方库环境可
安全 import 本包而不触发依赖缺失:
    from cloud_exec import CvmClient, TatClient, CosClient, SshClient, DiskClient

    cvm = CvmClient()
    info = cvm.open(image="img-xxx", types=["S5.LARGE16"], name="task")   # 竞价优先
    ssh = SshClient()                       # paramiko 懒加载
    rc, out, _ = ssh.ssh_run(info["public_ip"], "echo hello")
    cvm.terminate(info["instance_id"])
"""
from .auth import load_credentials, tc3_signature, tencent_call
from .cos import CosClient
from .cvm import CvmClient
from .disks import DiskClient
from .ssh import SshClient
from .tat import TatClient

__version__ = "0.3.0"

__all__ = ["CvmClient", "TatClient", "CosClient", "SshClient", "DiskClient",
           "load_credentials", "tc3_signature", "tencent_call", "__version__"]
