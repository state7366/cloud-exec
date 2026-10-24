# -*- coding: utf-8 -*-
"""disks.py —— 腾讯云 CBS 数据盘封装: 创建/挂载/卸载/销毁 + 实例端 mount + models 软链。

纯标准库(经 auth.tencent_call), 零 SDK 依赖。实例端 mount 经 TatClient 下发命令
(替代旧 deploy/dcommon/datadisk.py 的 ssh_run)。

用途: 模型权重放数据盘, 实例销毁只销毁系统盘, 数据盘卸载保留(免每轮 COS 重拉)。
"""
from __future__ import annotations

import time
from typing import Optional

from .auth import tencent_call

CBS_HOST = "cbs.tencentcloudapi.com"
CBS_VERSION = "2017-03-12"


class DiskClient:
    def __init__(self, region: str = "ap-shanghai", secret_id: str = "",
                 secret_key: str = "", tat=None, ssh=None):
        """tat: 可选的 TatClient；ssh: 可选的 SshClient。mount_on_instance 二选一。"""
        self.region = region
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.tat = tat
        self.ssh = ssh

    def _call(self, action, payload):
        return tencent_call("cbs", CBS_HOST, CBS_VERSION, action, payload,
                            self.region, self.secret_id, self.secret_key)

    # ---- CBS 侧 ----
    def describe(self, disk_id: str) -> dict:
        """查单盘, 返回 DiskSet[0] 或 {}。"""
        r = self._call("DescribeDisks", {"DiskIds": [disk_id]})
        return (r.get("DiskSet") or [{}])[0]

    def find_by_name(self, name: str) -> Optional[dict]:
        """按盘名精确查找, 返回盘 dict 或 None。"""
        r = self._call("DescribeDisks", {"Filters": [{"Name": "disk-name", "Values": [name]}]})
        ds = r.get("DiskSet") or []
        return ds[0] if ds else None

    def create(self, name: str, size: int = 200, disk_type: str = "CLOUD_SSD",
               zone: str = "ap-shanghai-2") -> str:
        """创建数据盘(按量计费), 返回 disk_id。"""
        r = self._call("CreateDisks", {
            "DiskChargeType": "POSTPAID_BY_HOUR",
            "Placement": {"Zone": zone},
            "DiskType": disk_type, "DiskSize": size, "DiskName": name})
        return (r.get("DiskIdSet") or [None])[0]

    def attach(self, disk_id: str, instance_id: str, timeout: int = 180) -> None:
        """挂载盘到实例 + 轮询到 Attached=True。"""
        d = self.describe(disk_id)
        if d.get("Attached") and d.get("InstanceId") == instance_id:
            return
        self._call("AttachDisks", {"DiskIds": [disk_id], "InstanceId": instance_id})
        deadline = time.time() + timeout
        while time.time() < deadline:
            d = self.describe(disk_id)
            if d.get("Attached"):
                return
            time.sleep(3)
        raise RuntimeError(f"[disks] attach {disk_id} 超时")

    def detach(self, disk_id: str, timeout: int = 120) -> None:
        """卸载盘 + 轮询到 Attached=False。"""
        d = self.describe(disk_id)
        if not d.get("Attached"):
            return
        self._call("DetachDisks", {"DiskIds": [disk_id]})
        deadline = time.time() + timeout
        while time.time() < deadline:
            d = self.describe(disk_id)
            if not d.get("Attached"):
                return
            time.sleep(3)
        raise RuntimeError(f"[disks] detach {disk_id} 超时")

    def terminate(self, disk_id: str) -> dict:
        """销毁数据盘(UNATTACHED 才可销毁)。"""
        return self._call("TerminateDisks", {"DiskIds": [disk_id]})

    # ---- 实例端 mount(SSH 或 TAT 二选一) ----
    def _mount_script(self, mount_point: str, models_link: Optional[str]) -> str:
        softlink = ""
        if models_link:
            softlink = f"""
if [ -d {models_link} ] && [ ! -L {models_link} ]; then
  if [ -d {mount_point}/models ]; then
    mv {models_link} {models_link}.sysdisk.bak
  else
    mv {models_link} {mount_point}/models
  fi
fi
mkdir -p {mount_point}/models
ln -sfn {mount_point}/models {models_link}
"""
        return f"""
set -e
sudo mkdir -p {mount_point}
if ! mountpoint -q {mount_point}; then
  DEV=$(lsblk -dpno NAME,TYPE | awk '$2=="disk" {{print $1}}' | grep -v /dev/vda | head -1 || true)
  if [ -z "$DEV" ]; then echo "NO_FREE_DISK"; exit 0; fi
  if ! sudo blkid $DEV >/dev/null 2>&1; then
    sudo mkfs.ext4 -F $DEV
  fi
  sudo mount $DEV {mount_point}
  sudo chown ubuntu:ubuntu {mount_point}
fi
{softlink}
df -h {mount_point} | tail -1
"""

    def mount_on_instance(self, instance_id: str, mount_point: str = "/data",
                          models_link: Optional[str] = None, ip: str = "",
                          timeout: int = 600) -> dict:
        """实例端: 找数据盘设备 → 格式化(仅首次) → mount → 可选 models 软链。

        models_link: 需软链到数据盘的本地目录(如 /home/ubuntu/dockbuddy/models),
        非空则 `ln -sfn {mount_point}/models {models_link}`。
        通道: 有 ssh + ip → SSH; 否则 TAT(需 tat)。标准镜像无 TAT agent 时用 SSH。
        返回 {"output": ...} 或 TAT run_and_wait 的结果。
        """
        script = self._mount_script(mount_point, models_link)
        if self.ssh is not None and ip:
            rc, out, err = self.ssh.ssh_run(ip, script, timeout=timeout, echo=True)
            if rc != 0:
                raise RuntimeError(f"[disks] 实例端挂载失败 rc={rc}: {out[-500:]} {err[-300:]}")
            return {"output": out}
        if self.tat is None:
            raise RuntimeError("[disks] mount_on_instance 需要 TatClient 或 SshClient(+ip)")
        return self.tat.run_and_wait(instance_id, script, timeout=timeout)

    def ensure(self, name: str, instance_id: str, mount_point: str = "/data",
               models_link: Optional[str] = None, size: int = 200,
               disk_type: str = "CLOUD_SSD", zone: str = "ap-shanghai-2",
               ip: str = "", timeout: int = 600) -> str:
        """一站式: 找到/创建盘 → 挂载到实例 → 实例端 mount + 软链。返回 disk_id。"""
        d = self.find_by_name(name)
        if d:
            disk_id = d["DiskId"]
        else:
            disk_id = self.create(name, size, disk_type, zone)
            for _ in range(60):
                if self.describe(disk_id).get("DiskState") == "UNATTACHED":
                    break
                time.sleep(3)
        self.attach(disk_id, instance_id)
        self.mount_on_instance(instance_id, mount_point, models_link, ip, timeout)
        return disk_id


__all__ = ["DiskClient"]
