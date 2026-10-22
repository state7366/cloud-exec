# -*- coding: utf-8 -*-
"""cvm.py —— 腾讯云 CVM 封装: 开实例(竞价优先)/查询/释放。

纯标准库(经 auth.tencent_call), 零 SDK 依赖, 与 DockBuddy 解耦。
"""
from __future__ import annotations

import time
from typing import List, Optional

from .auth import tencent_call

CVM_HOST = "cvm.tencentcloudapi.com"
CVM_VERSION = "2017-03-12"


class CvmClient:
    def __init__(self, region: str = "ap-shanghai", secret_id: str = "", secret_key: str = ""):
        self.region = region
        self.secret_id = secret_id
        self.secret_key = secret_key

    def _call(self, action, payload):
        return tencent_call("cvm", CVM_HOST, CVM_VERSION, action, payload,
                            self.region, self.secret_id, self.secret_key)

    def open(self, image: str, types: List[str], name: str = "cloud-exec-task",
             zones: Optional[List[str]] = None, disk_size: int = 100,
             disk_type: str = "CLOUD_SSD", password: str = "",
             charge_mode: str = "auto", user_data: str = "",
             bandwidth: int = 100) -> dict:
        """开实例, 竞价优先回退按量(charge_mode: auto/spot/paid)。

        返回 {instance_id, instance_type, charge_type, zone, public_ip}。
        """
        zones = zones or ["ap-shanghai-5", "ap-shanghai-2", "ap-shanghai-8"]
        modes = {"auto": ["SPOTPAID", "POSTPAID_BY_HOUR"],
                 "spot": ["SPOTPAID"], "paid": ["POSTPAID_BY_HOUR"]}.get(charge_mode)
        if not modes:
            raise ValueError(f"未知 charge_mode: {charge_mode}")

        base = {"ImageId": image, "InstanceCount": 1, "InstanceName": name,
                "SystemDisk": {"DiskType": disk_type, "DiskSize": disk_size},
                "InternetAccessible": {"InternetMaxBandwidthOut": bandwidth,
                                       "PublicIpAssigned": True}}
        if password:
            base["LoginSettings"] = {"Password": password}
        if user_data:
            import base64
            base["UserData"] = base64.b64encode(user_data.encode("utf-8")).decode("utf-8")

        ins = used = charge = zone = ""
        for mode in modes:
            for z in zones:
                for it in types:
                    payload = dict(base)
                    payload.update({"InstanceType": it, "InstanceChargeType": mode,
                                    "Placement": {"Zone": z}})
                    try:
                        r = self._call("RunInstances", payload)
                        ins = (r.get("InstanceIdSet") or [None])[0]
                        if ins:
                            used, charge, zone = it, mode, z
                            break
                    except Exception as e:  # noqa: BLE001
                        print(f"[warn] {z} {it} {mode} 失败: {e}", flush=True)
                    time.sleep(1)
                if ins:
                    break
            if ins:
                break
        if not ins:
            raise RuntimeError(f"[open] 所有机型均失败(charge_mode={charge_mode})")

        ip = self.wait_public(ins)
        return {"instance_id": ins, "instance_type": used, "charge_type": charge,
                "zone": zone, "public_ip": ip}

    def wait_public(self, instance_id: str, timeout: int = 600) -> str:
        """等实例 RUNNING, 返回公网 IP(超时返回空串)。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            inst = self.describe(instance_id)
            if inst and inst.get("InstanceState") == "RUNNING":
                pub = inst.get("PublicIpAddresses") or []
                return pub[0] if pub else ""
            time.sleep(8)
        return ""

    def describe(self, instance_id: str) -> dict:
        """查单实例状态, 返回 InstanceSet[0] 或 {}。"""
        r = self._call("DescribeInstances", {"InstanceIds": [instance_id]})
        return (r.get("InstanceSet") or [{}])[0]

    def list_by_name(self, name: str) -> list:
        """按实例名查实例列表。"""
        r = self._call("DescribeInstances",
                       {"Filters": [{"Name": "instance-name", "Values": [name]}]})
        return r.get("InstanceSet", [])

    def terminate(self, instance_id: str) -> dict:
        """释放实例(退还, 立即停止计费)。"""
        return self._call("TerminateInstances", {"InstanceIds": [instance_id]})
