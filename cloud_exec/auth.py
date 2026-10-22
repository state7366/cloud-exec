# -*- coding: utf-8 -*-
"""auth.py —— 腾讯云 TC3-HMAC-SHA256 签名 + 通用 API 调用 + 凭证读取。

纯标准库(零第三方依赖), 与 DockBuddy 项目完全解耦。任何需要调腾讯云 API
(CVM/TAT/COS 等)的场景都可复用。

凭证优先级: 环境变量 CLOUD_SECRET_ID/KEY > TENCENT_SECRET_ID/KEY > ~/.tccli/default.credential。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Tuple
from urllib import request


def load_credentials() -> Tuple[str, str]:
    """读腾讯云凭证。优先级: CLOUD_SECRET_* > TENCENT_SECRET_* > ~/.tccli/default.credential。"""
    sid = os.environ.get("CLOUD_SECRET_ID", "") or os.environ.get("TENCENT_SECRET_ID", "")
    skey = os.environ.get("CLOUD_SECRET_KEY", "") or os.environ.get("TENCENT_SECRET_KEY", "")
    if sid and skey:
        return sid, skey
    path = os.path.expanduser("~/.tccli/default.credential")
    if os.path.isfile(path):
        try:
            cfg = json.load(open(path, encoding="utf-8"))
            sid = cfg.get("secretId", "")
            skey = cfg.get("secretKey", "")
        except (OSError, ValueError):
            pass
    if not sid or not skey:
        raise RuntimeError(
            "缺少腾讯云凭证: 请设 CLOUD_SECRET_ID/KEY 或 TENCENT_SECRET_ID/KEY, "
            "或放 ~/.tccli/default.credential")
    return sid, skey


def tc3_signature(secret_id: str, secret_key: str, service: str, host: str,
                  payload: str, timestamp: int) -> str:
    """TC3-HMAC-SHA256 签名(与腾讯云官方 SDK 同口径)。"""
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    sd = hmac.new(("TC3" + secret_key).encode(), date.encode(), hashlib.sha256).digest()
    ss = hmac.new(sd, service.encode(), hashlib.sha256).digest()
    sk = hmac.new(ss, b"tc3_request", hashlib.sha256).digest()
    canonical_headers = "content-type:application/json; charset=utf-8\nhost:" + host + "\n"
    signed_headers = "content-type;host"
    canonical_request = "\n".join(["POST", "/", "", canonical_headers, signed_headers,
                                   hashlib.sha256(payload.encode()).hexdigest()])
    scope = date + "/" + service + "/tc3_request"
    string_to_sign = "\n".join(["TC3-HMAC-SHA256", str(timestamp), scope,
                                hashlib.sha256(canonical_request.encode()).hexdigest()])
    signature = hmac.new(sk, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return ("TC3-HMAC-SHA256 Credential=" + secret_id + "/" + scope +
            ", SignedHeaders=" + signed_headers + ", Signature=" + signature)


def tencent_call(service: str, host: str, version: str, action: str,
                 payload: dict, region: str,
                 secret_id: str = "", secret_key: str = "", timeout: int = 60) -> dict:
    """腾讯云 API 通用调用(TC3 签名 POST)。version: CVM=2017-03-12, TAT=2020-10-28。"""
    if not secret_id:
        secret_id, secret_key = load_credentials()
    ts = int(time.time())
    body = json.dumps(payload)
    auth = tc3_signature(secret_id, secret_key, service, host, body, ts)
    req = request.Request(
        "https://" + host + "/", data=body.encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-TC-Action": action,
            "X-TC-Version": version,
            "X-TC-Timestamp": str(ts),
            "X-TC-Region": region,
            "Authorization": auth,
        }, method="POST")
    with request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    r = data.get("Response", data)
    if isinstance(r, dict) and "Error" in r:
        raise RuntimeError(r["Error"].get("Code", "") + ": " + r["Error"].get("Message", ""))
    return r
