"""FastAPI 入口：提供裁决 API 与静态页面。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .adjudication import AdjudicationError, adjudicate, diagnose

app = FastAPI(title="本地赛道计次裁决台", version="1.0.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.post("/api/adjudicate")
async def api_adjudicate(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400, content={"ok": False, "error": "请求体不是合法的 JSON"}
        )
    try:
        result = adjudicate(payload)
    except AdjudicationError as exc:
        # 非法输入：返回错误，不展示任何部分结果。
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return JSONResponse(content={"ok": True, "result": result})


@app.post("/api/diagnose")
async def api_diagnose(request: Request) -> JSONResponse:
    """替代性诊断：请求体与 /api/adjudicate 完全相同（同一套校验与状态码）。

    纯只读分析：不改变任何裁决行为，只针对各选手的未采纳记录给出
    “同优可替代 / 仅次优可用 / 无法成趟”分类及同优替代方案。
    """
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400, content={"ok": False, "error": "请求体不是合法的 JSON"}
        )
    try:
        result = diagnose(payload)
    except AdjudicationError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return JSONResponse(content={"ok": True, "result": result})


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
