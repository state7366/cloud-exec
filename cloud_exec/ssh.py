# -*- coding: utf-8 -*-
"""ssh.py —— SSH 远程执行封装（paramiko 可选依赖，懒加载）。

纯标准库核心（import 本模块零第三方依赖）；paramiko 只在真正建立连接时 import，
故 n8n 沙箱等无 paramiko 环境可安全 `import cloud_exec.ssh` 而不触发依赖缺失。

从 DockBuddy 的 tools/deploy/_ssh.py 迁入（原 deploy/dcommon/ssh.py），保留全部
生产实测的坑位修复：keepalive、channel 超时防挂死、sftp 抖动重试、stall 检测、
base64 传 cmd 绕引号展开等。
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Tuple

_DEFAULT_USER = "ubuntu"
_DEFAULT_PASSWORD = "Dockbuddy@2026"

# sftp 偶发 EOFError / 10054 跨公网抖动，长目录上传几乎必现，统一重试。
_SFTP_RETRIES = 4
_SFTP_BACKOFF = (2, 5, 12)  # 秒
# 单次 sftp 读写超时：paramiko channel 默认无超时，半开连接会让 sftp.put 永久阻塞。
_SFTP_IO_TIMEOUT = 180
_SLOW_PUT_S = 60


class SshClient:
    """SSH/SFTP 执行客户端。user/password 构造时传入，方法不再带。"""

    def __init__(self, user: str = _DEFAULT_USER, password: str = _DEFAULT_PASSWORD):
        self.user = user
        self.password = password

    # ---- 连接 ----
    def _connect(self, ip: str):
        import paramiko
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        last = None
        for _ in range(20):
            try:
                c.connect(ip, username=self.user, password=self.password, timeout=20,
                          banner_timeout=20, auth_timeout=20)
                # keepalive：跨公网长任务（拉权重/装依赖可达 1h+）中间长时间无数据，
                # 中间设备会静默掐断半开连接，keepalive 让死连接尽快暴露。
                try:
                    c.get_transport().set_keepalive(30)
                except Exception:  # noqa: BLE001
                    pass
                return c
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(6)
        raise RuntimeError(f"SSH 连接失败: {last}")

    # ---- 执行 ----
    def ssh_run(self, ip: str, script: str, timeout: int = 3600,
                echo: bool = False) -> Tuple[int, str, str]:
        """执行 bash 脚本，返回 (rc, stdout, stderr)。echo=True 实时打印。"""
        import threading
        c = self._connect(ip)
        try:
            _i, o, e = c.exec_command("bash -s", timeout=timeout)
            o.channel.settimeout(timeout)
            _i.write(script)
            _i.channel.shutdown_write()
            out_buf: list = []
            err_buf: list = []

            def _drain(f, buf):
                try:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        buf.append(chunk)
                        if echo:
                            print(chunk.decode("utf-8", "replace"), end="", flush=True)
                except Exception:  # noqa: BLE001
                    pass

            t1 = threading.Thread(target=_drain, args=(o, out_buf))
            t2 = threading.Thread(target=_drain, args=(e, err_buf))
            t1.start(); t2.start()
            rc = o.channel.recv_exit_status()
            t1.join(timeout=15); t2.join(timeout=15)
            out = b"".join(out_buf).decode("utf-8", "replace")
            err = b"".join(err_buf).decode("utf-8", "replace")
            return rc, out, err
        finally:
            c.close()

    def ssh_run_bg(self, ip: str, cmd: str, log_path: str,
                   done_marker: Optional[str] = None) -> str:
        """nohup 后台启动 cmd，输出重定向 log_path；done_marker 结束写退出码。

        退出码不 echo 到日志（避免污染 test.py 约定的「最后一行单行 JSON」）；
        cmd 经 base64 传，绕开外层 bash -s 提前展开 $CONDA_BIN/$(...) 的坑。
        """
        import base64
        if done_marker:
            inner = f"{cmd}; echo $? > {done_marker}"
            b64 = base64.b64encode(inner.encode("utf-8")).decode("ascii")
            wrapped = (f"rm -f {done_marker} {log_path}; nohup bash -c "
                       f"\"echo {b64} | base64 -d | bash\" "
                       f"> {log_path} 2>&1 & echo STARTED")
        else:
            b64 = base64.b64encode(cmd.encode("utf-8")).decode("ascii")
            wrapped = (f"rm -f {log_path}; nohup bash -c \"echo {b64} | base64 -d | bash\" "
                       f"> {log_path} 2>&1 & echo STARTED")
        _rc, out, _ = self.ssh_run(ip, wrapped, timeout=60, echo=False)
        return out

    def tail_log(self, ip: str, log_path: str, n: int = 30) -> str:
        """拉远程日志尾部 n 行。"""
        _rc, out, _ = self.ssh_run(ip, f"tail -n {n} {log_path} 2>/dev/null",
                                   timeout=60, echo=False)
        return out

    def stream_log(self, ip: str, log_path: str, done_marker: Optional[str] = None,
                   timeout: int = 7200, poll: int = 2, stall_timeout: int = 300,
                   on_stall: Optional[Callable[[int], None]] = None,
                   cmd_pattern: Optional[str] = None) -> bool:
        """增量拉日志并实时打印，直到 done_marker 出现（或超时）。返回是否完成。

        含 stall 检测：日志 stall_timeout 秒无增长 → 警告/回调，避免远程卡住客户端傻等。
        """
        offset = 0
        last_growth = time.time()
        deadline = time.time() + timeout
        while time.time() < deadline:
            _rc, out, _ = self.ssh_run(ip, f"tail -c +{offset + 1} {log_path} 2>/dev/null",
                                       timeout=60, echo=False)
            if out:
                print(out, end="", flush=True)
                offset += len(out.encode("utf-8"))
                last_growth = time.time()
            idle = time.time() - last_growth
            if idle > stall_timeout:
                if on_stall:
                    on_stall(int(idle))
                else:
                    print(f"[warn] 远程日志 {int(idle)}s 无增长(可能卡住)", flush=True)
                    if cmd_pattern:
                        print(f"[diagnose] {self.diagnose_stall(ip, cmd_pattern)}", flush=True)
                last_growth = time.time()
            if done_marker:
                _rc, m, _ = self.ssh_run(ip, f"test -f {done_marker} && echo DONE || echo RUNNING",
                                         timeout=60, echo=False)
                if "DONE" in m:
                    _rc, out, _ = self.ssh_run(ip, f"tail -c +{offset + 1} {log_path} 2>/dev/null",
                                               timeout=60, echo=False)
                    if out:
                        print(out, end="", flush=True)
                    return True
            time.sleep(poll)
        return False

    def wait_marker(self, ip: str, marker: str, timeout: int = 7200, poll: int = 20,
                    on_progress: Optional[Callable[[], None]] = None) -> bool:
        """轮询 marker 文件是否出现，返回是否在 timeout 内完成。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            _rc, out, _ = self.ssh_run(ip, f"test -f {marker} && echo DONE || echo RUNNING",
                                       timeout=60, echo=False)
            if "DONE" in out:
                return True
            if on_progress:
                try:
                    on_progress()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(poll)
        return False

    def diagnose_stall(self, ip: str, pattern: str) -> str:
        """诊断远程进程卡住的根因（stall 时调用）。"""
        _rc, out, _ = self.ssh_run(ip, f"pgrep -f '{pattern}' | head -3",
                                   timeout=60, echo=False)
        pids = [p for p in out.split() if p]
        if not pids:
            return "进程已消失(可能异常退出, 查日志末尾错误)"
        pid = pids[0]
        parts = [f"pid={pid}"]
        _rc, out, _ = self.ssh_run(ip, f"sudo cat /proc/{pid}/status 2>/dev/null | grep State; "
                                       f"sudo cat /proc/{pid}/wchan 2>/dev/null",
                                   timeout=60, echo=False)
        if out.strip():
            parts.append(out.strip().replace("\n", " | "))
        _rc, out, _ = self.ssh_run(ip, f"sudo ls -l /proc/{pid}/fd 2>/dev/null "
                                       f"| grep -oE '\\.partial|socket:\\[[0-9]+\\]' | head -6",
                                   timeout=60, echo=False)
        if ".partial" in out:
            parts.append("检测到 .partial 文件 → 卡在 torch.hub 外网下载")
        elif "socket" in out:
            parts.append("有 socket 连接 → 可能在等网络")
        return "; ".join(parts)

    # ---- sftp ----
    @staticmethod
    def _is_transient(e: Exception) -> bool:
        """判断是否为可重试的传输层抖动（非文件本身问题）。"""
        import errno as _errno
        import socket
        if isinstance(e, (EOFError, socket.timeout, TimeoutError, ConnectionError)):
            return True
        _NET = {_errno.ETIMEDOUT, _errno.ECONNRESET, _errno.ECONNREFUSED,
                _errno.ECONNABORTED, _errno.EPIPE, _errno.EHOSTUNREACH,
                _errno.ENETUNREACH, _errno.ENETDOWN, _errno.ENOTCONN}
        for _n in ("EBADFD", "EINTR", "EAGAIN", "EHOSTDOWN"):
            _v = getattr(_errno, _n, None)
            if _v is not None:
                _NET.add(_v)
        if isinstance(e, OSError):
            return e.errno in _NET
        msg = str(e).lower()
        return any(k in msg for k in ("eof", "reset", "timed out", "10054",
                                      "connection aborted", "socket is closed", "closed"))

    @staticmethod
    def _mkdir_p(sftp, remote_dir: str) -> None:
        parts = [p for p in remote_dir.split("/") if p]
        cur = ""
        for p in parts:
            cur += "/" + p
            try:
                sftp.mkdir(cur)
            except OSError:
                pass

    def _walk_put(self, sftp, local_dir: str, remote_dir: str, exts: tuple) -> None:
        import os
        for name in os.listdir(local_dir):
            lp = os.path.join(local_dir, name)
            rp = remote_dir + "/" + name
            if os.path.isdir(lp):
                if name in ("__pycache__", ".git", "assets", "checkpoint"):
                    continue
                try:
                    self._mkdir_p(sftp, rp)
                except OSError:
                    pass
                self._walk_put(sftp, lp, rp, exts)
            elif exts is None or name.endswith(exts):
                t0 = time.time()
                try:
                    sftp.put(lp, rp)
                except OSError as e:
                    if self._is_transient(e):
                        raise
                    print(f"[sftp] 跳过 {rp}: {type(e).__name__}: {e}", flush=True)
                dt = time.time() - t0
                if dt > _SLOW_PUT_S:
                    print(f"[sftp][warn] {rp} 单文件耗时 {dt:.0f}s(>{_SLOW_PUT_S}s), "
                          f"连接可能已劣化", flush=True)

    def sftp_get(self, ip: str, remote: str, local: str) -> None:
        """sftp 下载单个文件（自动建本地父目录；传输层抖动自动重试）。"""
        import os
        last = None
        for attempt in range(_SFTP_RETRIES):
            try:
                c = self._connect(ip)
                try:
                    sftp = c.open_sftp()
                    sftp.get_channel().settimeout(_SFTP_IO_TIMEOUT)
                    parent = os.path.dirname(local)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    sftp.get(remote, local)
                    sftp.close()
                finally:
                    c.close()
                return
            except Exception as e:  # noqa: BLE001
                last = e
                if not self._is_transient(e) or attempt == _SFTP_RETRIES - 1:
                    raise
                wait = _SFTP_BACKOFF[min(attempt, len(_SFTP_BACKOFF) - 1)]
                print(f"[sftp] 抖动重试 {attempt + 1}/{_SFTP_RETRIES - 1}: "
                      f"{type(e).__name__}; {wait}s 后重拉 {os.path.basename(remote)}", flush=True)
                time.sleep(wait)
        raise last

    def sftp_put(self, ip: str, local: str, remote: str) -> None:
        """sftp 上传单个文件（自动建父目录；传输层抖动自动重试）。"""
        import os
        last = None
        for attempt in range(_SFTP_RETRIES):
            try:
                c = self._connect(ip)
                try:
                    sftp = c.open_sftp()
                    sftp.get_channel().settimeout(_SFTP_IO_TIMEOUT)
                    parent = remote.rsplit("/", 1)[0]
                    if parent:
                        self._mkdir_p(sftp, parent)
                    sftp.put(local, remote)
                    sftp.close()
                finally:
                    c.close()
                return
            except Exception as e:  # noqa: BLE001
                last = e
                if not self._is_transient(e) or attempt == _SFTP_RETRIES - 1:
                    raise
                wait = _SFTP_BACKOFF[min(attempt, len(_SFTP_BACKOFF) - 1)]
                print(f"[sftp] 抖动重试 {attempt + 1}/{_SFTP_RETRIES - 1}: "
                      f"{type(e).__name__}; {wait}s 后重传 {os.path.basename(local)}", flush=True)
                time.sleep(wait)
        raise last

    def sftp_put_dir(self, ip: str, local_dir: str, remote_dir: str,
                     exts: tuple = (".py", ".sh", ".yaml", ".yml", ".txt", ".json")) -> None:
        """递归 sftp 上传目录（按扩展名过滤；抖动自动重试整个目录）。exts=None 全传。"""
        import os
        last = None
        for attempt in range(_SFTP_RETRIES):
            try:
                c = self._connect(ip)
                try:
                    sftp = c.open_sftp()
                    sftp.get_channel().settimeout(_SFTP_IO_TIMEOUT)
                    self._mkdir_p(sftp, remote_dir)
                    self._walk_put(sftp, local_dir, remote_dir, exts)
                    sftp.close()
                finally:
                    c.close()
                return
            except Exception as e:  # noqa: BLE001
                last = e
                if not self._is_transient(e) or attempt == _SFTP_RETRIES - 1:
                    raise
                wait = _SFTP_BACKOFF[min(attempt, len(_SFTP_BACKOFF) - 1)]
                print(f"[sftp] 目录传输抖动重试 {attempt + 1}/{_SFTP_RETRIES - 1}: "
                      f"{type(e).__name__}; {wait}s 后重传 {os.path.basename(local_dir)}", flush=True)
                time.sleep(wait)
        raise last


__all__ = ["SshClient"]
