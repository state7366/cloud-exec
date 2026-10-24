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
        # (bucket 作为子域名 <bucket>.cos.<region>.tencentcos.cn 拼到前面, 与 qcloud_cos SDK 同口径)
        ep = endpoint or f"{bucket}.cos.{region}.myqcloud.com"
        if "tencentcos.cn" in ep and not ep.startswith(f"{bucket}."):
            ep = f"{bucket}.{ep}"
        self.endpoint = ep
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
        """下载对象到本地文件（纯标准库，流式分块写，不整读入内存）。"""
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        req = request.Request(self._url(key), headers={"Authorization": self._sign("get", key)},
                              method="GET")
        with request.urlopen(req, timeout=600) as resp:
            with open(dest_path, "wb") as f:
                for chunk in iter(lambda: resp.read(1 << 20), b""):
                    f.write(chunk)

    def download_file(self, key: str, dest_path: str, min_bytes: int = 1) -> bool:
        """流式下载到 dest_path；已存在且 >=min_bytes 跳过（幂等），返回是否真正下载。"""
        if os.path.isfile(dest_path) and os.path.getsize(dest_path) >= min_bytes:
            return False
        self.download(key, dest_path)
        return True

    def upload_file(self, key: str, local_path: str) -> None:
        """上传本地文件到 COS（qcloud_cos SDK 懒加载，自动 multipart 分片）。

        多 GB 大文件用 SDK 的 multipart（断点续传）；小文件也走这里（SDK 自动单次 PUT）。
        paramiko 类可选依赖：qcloud_cos 仅在真正上传时 import。
        """
        from qcloud_cos import CosConfig as QCosConfig
        from qcloud_cos import CosS3Client
        kw = dict(Region=self.region, SecretId=self.secret_id, SecretKey=self.secret_key,
                  Scheme=self.scheme)
        # 内网 endpoint（tencentcos.cn）才显式传；公网默认域名走 SDK 默认 endpoint
        if self.endpoint and "tencentcos.cn" in self.endpoint:
            kw["Endpoint"] = self.endpoint
        CosS3Client(QCosConfig(**kw)).upload_file(
            Bucket=self.bucket, Key=key, LocalFilePath=local_path)

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

    def head_etag(self, key: str) -> str:
        """查对象 ETag(去引号); 不存在返回 ''。

        单文件 PUT 的 ETag = 文件 MD5(可直接对比); multipart 分片上传的 ETag =
        '<md5>-<片数>'(复合值, 无法直接对比) —— 调用方遇 '-' 应视为「未知, 必须重传」。
        """
        req = request.Request(self._url(key), headers={"Authorization": self._sign("head", key)},
                              method="HEAD")
        try:
            with request.urlopen(req, timeout=60) as resp:
                return (resp.headers.get("ETag", "") or "").strip('"')
        except Exception:  # noqa: BLE001
            return ""
