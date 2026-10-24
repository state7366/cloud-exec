# -*- coding: utf-8 -*-
"""billing.py —— 腾讯云计费封装: 按量账单明细查询(成本核算用)。

纯标准库(经 auth.tencent_call), 零 SDK 依赖, 与 DockBuddy 解耦。
"""
from __future__ import annotations

from typing import List, Optional

from .auth import tencent_call

BILLING_HOST = "billing.tencentcloudapi.com"
BILLING_VERSION = "2018-07-09"


class BillingClient:
    def __init__(self, region: str = "ap-shanghai", secret_id: str = "", secret_key: str = ""):
        self.region = region
        self.secret_id = secret_id
        self.secret_key = secret_key

    def query_bill(self, begin: str, end: str, month: Optional[str] = None,
                   limit: int = 100, offset: int = 0) -> List[dict]:
        """查按量账单明细(DescribeBillDetail, byUsedTime)，返回 DetailSet 列表。

        每个条目含 ResourceName/ResourceId/BusinessCodeName + ComponentSet
        (每项含 RealCost)。账单有 ~1 小时延迟。
        """
        r = tencent_call("billing", BILLING_HOST, BILLING_VERSION, "DescribeBillDetail",
                         {"PeriodType": "byUsedTime", "Month": month or begin[:7],
                          "BeginTime": begin, "EndTime": end,
                          "Limit": limit, "Offset": offset},
                         self.region, self.secret_id, self.secret_key)
        return list(r.get("DetailSet") or [])

    def cost_by_resource(self, begin: str, end: str, month: Optional[str] = None) -> dict:
        """按资源名汇总 RealCost，返回 {资源名: 金额}。"""
        from collections import defaultdict
        out = defaultdict(float)
        for d in self.query_bill(begin, end, month):
            name = d.get("ResourceName") or d.get("ResourceId") or "?"
            for comp in (d.get("ComponentSet") or []):
                out[name] += float(comp.get("RealCost", 0) or 0)
        return dict(out)


__all__ = ["BillingClient"]
