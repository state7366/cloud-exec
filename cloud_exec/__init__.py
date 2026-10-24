# -*- coding: utf-8 -*-
"""cloud_exec —— 腾讯云执行层封装(CVM/TAT/COS), 纯标准库, 零第三方依赖。

与 DockBuddy 项目完全解耦。任何需要开 CVM 实例跑任务的场景都可复用:
    from cloud_exec import CvmClient, TatClient, CosClient

    cvm = CvmClient()
    info = cvm.open(image="img-xxx", types=["S5.LARGE16"], name="task")   # 竞价优先
    tat = TatClient()
    r = tat.run_and_wait(info["instance_id"], "echo hello")
    cvm.terminate(info["instance_id"])
"""
from .auth import load_credentials, tc3_signature, tencent_call
from .cos import CosClient
from .cvm import CvmClient
from .disks import DiskClient
from .tat import TatClient

__version__ = "0.2.0"

__all__ = ["CvmClient", "TatClient", "CosClient", "DiskClient",
           "load_credentials", "tc3_signature", "tencent_call", "__version__"]
