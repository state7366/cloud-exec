# -*- coding: utf-8 -*-
"""test_cloud_exec.py —— 不联网单元测试(签名/凭证/构造)。"""
import os
import unittest

from cloud_exec.auth import load_credentials, tc3_signature
from cloud_exec.cos import CosClient
from cloud_exec.cvm import CvmClient
from cloud_exec.tat import TatClient


class TestAuth(unittest.TestCase):
    def test_tc3_signature_format(self):
        sig = tc3_signature("AKID", "SECRET", "cvm", "cvm.tencentcloudapi.com", "{}", 1700000000)
        self.assertTrue(sig.startswith("TC3-HMAC-SHA256 Credential=AKID/"))
        self.assertIn("SignedHeaders=content-type;host", sig)
        self.assertIn("Signature=", sig)

    def test_load_credentials_env(self):
        os.environ["CLOUD_SECRET_ID"] = "testid"
        os.environ["CLOUD_SECRET_KEY"] = "testkey"
        sid, skey = load_credentials()
        self.assertEqual((sid, skey), ("testid", "testkey"))


class TestClients(unittest.TestCase):
    def test_construct(self):
        cvm = CvmClient(region="ap-shanghai", secret_id="a", secret_key="b")
        self.assertEqual(cvm.region, "ap-shanghai")
        tat = TatClient(secret_id="a", secret_key="b")
        self.assertEqual(tat.region, "ap-shanghai")
        cos = CosClient(bucket="b", endpoint="cos.ap-shanghai.tencentcos.cn",
                        secret_id="a", secret_key="b")
        self.assertEqual(cos.endpoint, "cos.ap-shanghai.tencentcos.cn")

    def test_cos_sign(self):
        cos = CosClient(bucket="b", secret_id="a", secret_key="b")
        sig = cos._sign("get", "key")
        self.assertTrue(sig.startswith("q-sign-algorithm=sha1&q-ak=a&"))

    def test_open_charge_mode(self):
        cvm = CvmClient(secret_id="a", secret_key="b")
        with self.assertRaises(ValueError):
            cvm.open(image="i", types=["t"], charge_mode="bad")


if __name__ == "__main__":
    unittest.main(verbosity=2)
