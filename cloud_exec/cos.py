# -*- coding: utf-8 -*-
"""cos.py —— 腾讯云 COS 对象存储封装: 上传/下载/查大小(V5 签名)。

纯标准库(无 SDK), 零依赖, 与 DockBuddy 解耦。支持内网/公网 endpoint。
内网铁律: 云实例访问 COS 走内网 endpoint(cos.<region>.tencentcos.cn, 流量 0 元)。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional
from urllib import request

from .auth import load_credentials


class CosClient:
    def __init__(self, bucket: str, region: str = "ap-shanghai",
                 endpoint: Optional[str] = None, scheme: str = "https",
                 secret_id: str = "", secret_key: str = ""):
        self.bucket = bucket
        self.region = region
        self.scheme = scheme
        # endpoint 空 = 公网默认(bucket.cos.region.myqcloud.com); 内网填 cos.<region>.tencentcos.cn
        self.endpoint = endpoint or f"{bucket}.cos.{region}.myqcloud.com"
        if secret_id:
            self.secret_id, self.secret_key = secret_id, secret_key
        else:
            self.secret_id, self.secret_key = load_credentials()

    def _sign(self, method: str, key: str) -> str:
        """COS V5 签名(SignKey 用 hexdigest, host 头参与签名)。"""
        host = self.endpoint
        path = "/" + key
        now = int(time.time())
        key_time = str(now - 60) + ";" + str(now + 3600)
        sign_key = hmac.new(self.secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
        http_string = method + "\n" + path + "\n\n" + "host=" + host + "\n"
        string_to_sign = ("sha1\n" + key_time + "\n" +
                          hashlib.sha1(http_string.encode()).hexdigest() + "\n")
        signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()
        return ("q-sign-algorithm=sha1&q-ak=" + self.secret_id + "&q-sign-time=" + key_time +
                "&q-key-time=" + key_time + "&q-header-list=host&q-url-param-list=&q-signature=" +
                signature)

    def _url(self, key: str) -> str:
        return self.scheme + "://" + self.endpoint + "/" + key

    def upload(self, key: str, local_path: str) -> None:
        """上传本地文件到 COS。"""
        with open(local_path, "rb") as f:
            data = f.read()
        req = request.Request(self._url(key), data=data, method="PUT",
                              headers={"Authorization": self._sign("put", key),
                                       "Content-Length": str(len(data))})
        with request.urlopen(req, timeout=600) as resp:
            if resp.status not in (200, 204):
                raise RuntimeError(f"COS PUT {resp.status}")

    def upload_bytes(self, key: str, data: bytes) -> None:
        req = request.Request(self._url(key), data=data, method="PUT",
                              headers={"Authorization": self._sign("put", key),
                                       "Content-Length": str(len(data))})
        with request.urlopen(req, timeout=600) as resp:
            if resp.status not in (200, 204):
                raise RuntimeError(f"COS PUT {resp.status}")

    def download(self, key: str, dest_path: str) -> None:
        """下载对象到本地文件。"""
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        req = request.Request(self._url(key), headers={"Authorization": self._sign("get", key)},
                              method="GET")
        with request.urlopen(req, timeout=600) as resp:
            with open(dest_path, "wb") as f:
                f.write(resp.read())

    def get_bytes(self, key: str) -> bytes:
        req = request.Request(self._url(key), headers={"Authorization": self._sign("get", key)},
                              method="GET")
        with request.urlopen(req, timeout=600) as resp:
            return resp.read()

    def head_size(self, key: str) -> int:
        """查对象字节数; 不存在返回 -1。"""
        req = request.Request(self._url(key), headers={"Authorization": self._sign("head", key)},
                              method="HEAD")
        try:
            with request.urlopen(req, timeout=60) as resp:
                return int(resp.headers.get("Content-Length", 0) or 0)
        except Exception:  # noqa: BLE001
            return -1
