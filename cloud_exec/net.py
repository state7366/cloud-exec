# -*- coding: utf-8 -*-
"""
net.py —— 本机坏 DNS 的 workaround(8.8.8.8 手动解析指定域名)
===================================================================================
背景: 本机默认 DNS 服务器坏(nslookup 全 timeout, 直连任何域名 getaddrinfo failed),
但网络层直连 8.8.8.8 可达。故对指定后缀的域名(如 *.tencentcloudapi.com)用 8.8.8.8
手动构造 DNS 报文解析 A 记录, 其它域名不干预。

云调用前置: 本机直连腾讯云 API 域名时先 patch_dns(("tencentcloudapi.com",))。
幂等: patch_dns() 全局只 patch 一次, 重复调用安全。
"""
from __future__ import annotations

import socket
import struct
from typing import List

_PATCHED = False


def dns_a(name: str, server: str = "8.8.8.8", port: int = 53, timeout: int = 6) -> List[str]:
    """手动构造 DNS A 记录查询, 返回 IP 列表(失败返回空列表)。"""
    try:
        import random
        tid = random.randint(0, 0xFFFF)
        labels = name.rstrip(".").split(".")
        qname = b"".join(struct.pack("B", len(l)) + l.encode("ascii") for l in labels) + b"\x00"
        header = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
        query = header + qname + struct.pack(">HH", 1, 1)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(query, (server, port))
            data, _ = s.recvfrom(4096)
        finally:
            s.close()
        if len(data) < 12:
            return []
        rcode = struct.unpack(">H", data[2:4])[0] & 0x0F
        ancount = struct.unpack(">H", data[6:8])[0]
        if rcode != 0 or ancount == 0:
            return []
        off = 12
        while off < len(data) and data[off] != 0:
            off += 1 + data[off]
        off += 1 + 4
        ips: List[str] = []
        for _ in range(ancount):
            if off + 10 > len(data):
                break
            if data[off] & 0xC0 == 0xC0:
                off += 2
            else:
                while off < len(data) and data[off] != 0:
                    off += 1 + data[off]
                off += 1
            if off + 10 > len(data):
                break
            rtype, _rc, _ttl, rdlen = struct.unpack(">HHIH", data[off:off + 10])
            off += 10
            rdata = data[off:off + rdlen]
            off += rdlen
            if rtype == 1 and rdlen == 4:
                ips.append(".".join(str(b) for b in rdata))
        return ips
    except Exception:  # noqa: BLE001 —— 解析失败回退系统解析
        return []


def patch_dns(suffixes=("tencentcloudapi.com",)) -> None:
    """对给定域名后缀做 8.8.8.8 手动解析(幂等, 全局一次)。"""
    global _PATCHED
    if _PATCHED:
        return
    _orig = socket.getaddrinfo

    def _patched(host, port, family=0, type=0, proto=0, flags=0):
        if isinstance(host, str) and host.endswith(suffixes):
            ips = dns_a(host)
            if ips:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
        return _orig(host, port, family, type, proto, flags)

    socket.getaddrinfo = _patched
    _PATCHED = True


__all__ = ["dns_a", "patch_dns"]
