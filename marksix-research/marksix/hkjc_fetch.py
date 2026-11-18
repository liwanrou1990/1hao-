"""香港赛马会官方结果采集（实验性，尚未验证）。

现状（2026-11-18）：
* 开发环境可以访问 bet.hkjc.com（结果页为前端渲染页面），但未在本环境中实际调用官方数据接口，
  因此接口的地址、参数和返回结构均未经验证。本模块不会臆造接口：
  - 默认不启用；
  - 用户须在本机浏览器打开官方结果页，确认数据请求的实际地址后，自行填入 HKJC_ENDPOINT 等配置；
  - 返回内容原样保存为快照（含哈希），解析失败即报错，不做猜测性解析。
* 推荐路径：从官方结果页导出/整理为 CSV，用 `python -m marksix import-csv` 导入（必须注明来源）。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

OFFICIAL_RESULTS_PAGE = "https://bet.hkjc.com/ch/marksix/results"


class NotConfigured(RuntimeError):
    pass


def fetch_raw(endpoint: str | None = None, payload: dict | None = None, timeout: int = 30) -> dict:
    """向用户确认过的官方端点发送请求，返回 {content, sha256, fetched_at, endpoint}。不做解析。"""
    endpoint = endpoint or os.environ.get("HKJC_ENDPOINT")
    if not endpoint:
        raise NotConfigured(
            "未配置官方数据端点。请先在浏览器开发者工具中确认 " + OFFICIAL_RESULTS_PAGE +
            " 页面实际使用的数据请求，再设置环境变量 HKJC_ENDPOINT（及 HKJC_PAYLOAD_JSON）。"
            "在此之前请使用 CSV 导入。")
    import requests

    if payload is None and os.environ.get("HKJC_PAYLOAD_JSON"):
        payload = json.loads(os.environ["HKJC_PAYLOAD_JSON"])
    r = (requests.post(endpoint, json=payload, timeout=timeout) if payload
         else requests.get(endpoint, timeout=timeout))
    r.raise_for_status()
    return {"content": r.content, "sha256": hashlib.sha256(r.content).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(), "endpoint": endpoint}
