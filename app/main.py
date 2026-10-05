"""FastAPI 入口：提供裁决 API、设备日志合并 API 与静态页面。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .adjudication import AdjudicationError, adjudicate, diagnose
from .merge import build_preview, confirm_merge

app = FastAPI(title="本地赛道计次裁决台", version="1.1.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def _read_json(request: Request) -> Any:
    """读取 JSON 请求体；非 JSON / 非 UTF-8 时抛 AdjudicationError 交给统一 400 处理。"""
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise AdjudicationError("请求体不是合法的 JSON")


def _ok(result: Any) -> JSONResponse:
    return JSONResponse(content={"ok": True, "result": result})


def _bad_request(exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.post("/api/adjudicate")
async def api_adjudicate(request: Request) -> JSONResponse:
    try:
        payload = await _read_json(request)
    except AdjudicationError as exc:
        return _bad_request(exc)
    try:
        result = adjudicate(payload)
    except AdjudicationError as exc:
        # 非法输入：返回错误，不展示任何部分结果。
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return _ok(result)


@app.post("/api/diagnose")
async def api_diagnose(request: Request) -> JSONResponse:
    """替代性诊断：请求体与 /api/adjudicate 完全相同（同一套校验与状态码）。

    纯只读分析：不改变任何裁决行为，只针对各选手的未采纳记录给出
    “同优可替代 / 仅次优可用 / 无法成趟”分类及同优替代方案。
    """
    try:
        payload = await _read_json(request)
    except AdjudicationError as exc:
        return _bad_request(exc)
    try:
        result = diagnose(payload)
    except AdjudicationError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return _ok(result)


@app.post("/api/merge/preview")
async def api_merge_preview(request: Request) -> JSONResponse:
    """设备 CSV 并入预览：请求体 = 裁决载荷 + csv 字段（UTF-8 文本）。

    整批式校验：当前表非法、CSV 表头/行非法、未知选手、同 ID 冲突、合并后
    超过 40 条均返回 422 中文原因、不产生任何部分结果；合法时返回新增/重复
    清单及一次性指纹令牌，供确认端证明“确认输入与预览一致”。
    """
    try:
        payload = await _read_json(request)
    except AdjudicationError as exc:
        return _bad_request(exc)
    try:
        result = build_preview(payload)
    except AdjudicationError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return _ok(result)


@app.post("/api/merge/confirm")
async def api_merge_confirm(request: Request) -> JSONResponse:
    """设备 CSV 并入确认：请求体 = 预览请求体 + token（预览所得指纹）。

    服务端无状态重算并比对指纹：任何赛道输入或 CSV 内容变化、令牌缺失都按
    422 拒绝（要求重新预览）；一致时返回与预览相同的合并结果，由前端追加。
    """
    try:
        payload = await _read_json(request)
    except AdjudicationError as exc:
        return _bad_request(exc)
    try:
        result = confirm_merge(payload)
    except AdjudicationError as exc:
        # MergeError 与 AdjudicationError 同一套 422 语义：整批拒绝、原表保留。
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return _ok(result)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
