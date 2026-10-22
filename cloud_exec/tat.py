# -*- coding: utf-8 -*-
"""tat.py —— 腾讯云 TAT(自动化助手)封装: 下发命令 / 查询任务 / 轮询。

纯标准库(经 auth.tencent_call), 零 SDK 依赖, 与 DockBuddy 解耦。
"""
from __future__ import annotations

import base64
import time
from typing import Optional

from .auth import tencent_call

TAT_HOST = "tat.tencentcloudapi.com"
TAT_VERSION = "2020-10-28"

_DONE = {"SUCCESS", "FAILED", "TIMEOUT", "TERMINATED"}


class TatClient:
    def __init__(self, region: str = "ap-shanghai", secret_id: str = "", secret_key: str = ""):
        self.region = region
        self.secret_id = secret_id
        self.secret_key = secret_key

    def _call(self, action, payload):
        return tencent_call("tat", TAT_HOST, TAT_VERSION, action, payload,
                            self.region, self.secret_id, self.secret_key)

    def run(self, instance_id: str, command: str, timeout: int = 86400,
            workdir: str = "/tmp", command_type: str = "SHELL") -> str:
        """下发命令, 返回 InvocationId。command 为明文 shell, 内部自动 base64。"""
        content = base64.b64encode(command.encode("utf-8")).decode("utf-8")
        r = self._call("RunCommand", {
            "Content": content, "InstanceIds": [instance_id],
            "CommandType": command_type, "Timeout": timeout,
            "WorkingDirectory": workdir})
        inv = r.get("InvocationId", "")
        if not inv:
            raise RuntimeError("TAT RunCommand 未返回 InvocationId")
        return inv

    def describe(self, invocation_id: str) -> dict:
        """查命令任务状态, 返回 {status, exit_code, output}。"""
        r = self._call("DescribeInvocationTasks", {
            "Filters": [{"Name": "invocation-id", "Values": [invocation_id]}]})
        t = (r.get("InvocationTaskSet") or [None])[0] or {}
        result = t.get("TaskResult") or {}
        output = result.get("Output", "") or ""
        try:
            output = base64.b64decode(output).decode("utf-8")
        except Exception:  # noqa: BLE001
            pass
        return {"status": t.get("TaskStatus", "PENDING") or "PENDING",
                "exit_code": result.get("ExitCode"),
                "output": output}

    def wait(self, invocation_id: str, timeout: int = 3600,
             poll_seconds: int = 10) -> dict:
        """轮询直到完成, 返回 describe() 结果。超时抛 RuntimeError。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.describe(invocation_id)
            if st["status"] in _DONE:
                return st
            time.sleep(poll_seconds)
        raise RuntimeError(f"TAT 任务轮询超时(>{timeout}s), invocationId={invocation_id}")

    def run_and_wait(self, instance_id: str, command: str, timeout: int = 3600,
                     workdir: str = "/tmp") -> dict:
        """下发并等待完成, 返回 {status, exit_code, output}。"""
        inv = self.run(instance_id, command, timeout=timeout, workdir=workdir)
        return self.wait(inv, timeout=timeout)
