# cloud-exec

腾讯云执行层封装：CVM 开实例 / TAT 下发命令 / COS 对象存储。**纯标准库，零第三方依赖**，与 DockBuddy 项目完全解耦。

## 安装

```bash
pip install cloud-exec \
  --index-url https://pypi.cnb.cool/vcinex/bioinfo/-/packages/simple
```

## 快速开始

```python
from cloud_exec import CvmClient, TatClient, CosClient

# 开实例(竞价优先回退按量)
cvm = CvmClient(region="ap-shanghai")
info = cvm.open(image="img-xxx", types=["S5.LARGE16", "GN6S.LARGE20"], name="my-task")
print(info)   # {'instance_id': ..., 'instance_type': ..., 'charge_type': ..., 'public_ip': ...}

# 下发命令并等待完成
tat = TatClient()
r = tat.run_and_wait(info["instance_id"], "echo hello")
print(r["status"], r["output"])

# 释放实例
cvm.terminate(info["instance_id"])

# COS(内网 endpoint 流量 0 元)
cos = CosClient(bucket="my-bucket", endpoint="cos.ap-shanghai.tencentcos.cn", scheme="http")
cos.upload("path/to/key", "local_file.txt")
cos.download("path/to/key", "out.txt")
```

## 凭证

优先级：环境变量 `CLOUD_SECRET_ID/KEY` > `TENCENT_SECRET_ID/KEY` > `~/.tccli/default.credential`。

## API

| 类 | 方法 | 说明 |
|---|---|---|
| `CvmClient` | `open(image, types, ...)` | 开实例（竞价优先，charge_mode=auto/spot/paid） |
| | `wait_public(id)` / `describe(id)` / `list_by_name(name)` | 查询实例 |
| | `terminate(id)` | 释放实例 |
| `TatClient` | `run(id, cmd)` / `describe(inv)` / `wait(inv)` | 下发/查询/轮询命令 |
| | `run_and_wait(id, cmd)` | 下发并等待完成 |
| `CosClient` | `upload(key, path)` / `download(key, path)` | 上传/下载 |
| | `get_bytes(key)` / `head_size(key)` | 读字节/查大小 |
| `auth` | `tencent_call(service, host, version, action, payload, region)` | 通用腾讯云 API 调用 |

## 与 DockBuddy 的关系

本包是从 DockBuddy 的 `deploy/dcommon/instance.py` + `deploy/n8n/workflow/_prelude.py` 里
「纯腾讯云 API」部分抽出来的独立封装，不含任何 DockBuddy 的模型/环境/路径逻辑。
