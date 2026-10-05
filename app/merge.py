"""设备感应日志 CSV 并入当前记录表：UTF-8 CSV 解析、差异预览与确认令牌。

两步均为无状态 HTTP（服务端不保存任何会话状态）：
- 预览：当前赛道输入（与 /api/adjudicate 同一份载荷）+ 上传 CSV 文本，
  服务端按同一套校验解析当前表，再比对 CSV，返回新增 / 重复清单与一次性指纹令牌；
- 确认：请求体与预览完全相同并附预览令牌，服务端重算指纹比对一致后才返回合并结果，
  前端据此把新记录写入表格。任何赛道输入或文件内容变化都会令令牌失配。

合并规则：
- CSV 为 UTF-8 文本（允许开头一个 BOM），表头必须恰好为 id,runner,gate,time，
  字段支持 RFC 4180 风格双引号（含引号转义、字段内逗号与换行）；
- ID 沿用现有去空白规则；同 ID 在文件内或与现有记录内容一致（编号、秒数按
  精确整数比较，门按精确文本）的条目跳过；同 ID 内容冲突整批拒绝；
- 未知选手、非法行、合并后有效记录超过 40 条同样整批拒绝，原表由调用方保留不动；
- 旧记录顺序不变，新增条目严格按文件首次出现顺序排列。
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
from typing import Any

from .adjudication import (
    GATES,
    MAX_RECORDS,
    AdjudicationError,
    _parse_nonneg_int,
    _parse_positive_int,
    parse_payload,
)

CSV_HEADER = ("id", "runner", "gate", "time")


class MergeError(AdjudicationError):
    """CSV 合并非法错误，message 为可直接展示的中文说明。"""


def _canonical_json(obj: Any) -> bytes:
    # 与键顺序、空白无关的确定性序列化：确认端只凭重算指纹比对，
    # 前端预览/确认两次请求只要语义一致即可（实际代码发送同一对象）。
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def fingerprint_token(body: Any) -> str:
    """对预览请求体（当前赛道输入 + csv 原文）取 SHA-256 十六进制指纹。"""
    return hashlib.sha256(_canonical_json(body)).hexdigest()


def _read_csv_rows(csv_text: str) -> list[tuple[int, list[str]]]:
    """用标准库 csv 解析，返回 (物理行号, 字段列表)，物理空白行跳过。

    物理行号含表头与跳过的空行，供错误与重复定位使用；跨行引号字段记其结束行。
    """
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text[1:]
    reader = csv.reader(io.StringIO(csv_text, newline=""))
    rows: list[tuple[int, list[str]]] = []
    try:
        for fields in reader:
            line = reader.line_num
            # 物理空白行（零或纯空白字符、无逗号）与全空占位行一并忽略，
            # 不计列数错误；四列全空的数据行跳过规则与页面记录表一致。
            if all(f.strip() == "" for f in fields):
                continue
            rows.append((line, fields))
    except csv.Error as exc:
        raise MergeError(f"CSV 第 {reader.line_num} 行附近格式错误（引号或换行不合法）：{exc}") from exc
    return rows


def parse_device_csv(csv_text: Any) -> list[tuple[int, str, int, str, int]]:
    """校验并解析设备 CSV，返回按文件顺序的 (物理行号, ID, 选手, 门, 秒数)。

    四列全空的数据行视为占位空行跳过（与页面记录表规则一致）；
    部分空、列数不对、编号/秒数不是整数、门非法均抛 MergeError。
    """
    if not isinstance(csv_text, str):
        raise MergeError("缺少上传的 CSV 文本（字段 csv 必须是 UTF-8 字符串）")

    rows = _read_csv_rows(csv_text)
    if not rows:
        raise MergeError("CSV 文件为空：第一行必须是表头 id,runner,gate,time")

    header_line, header = rows[0]
    if tuple(header) != CSV_HEADER:
        raise MergeError(
            f"CSV 表头（第 {header_line} 行）必须恰好为 id,runner,gate,time，"
            "顺序与列数均须一致（字段允许用双引号包裹）"
        )

    parsed: list[tuple[int, str, int, str, int]] = []
    for line, fields in rows[1:]:
        if len(fields) != 4:
            raise MergeError(f"CSV 第 {line} 行有 {len(fields)} 列，必须恰好为 4 列：id,runner,gate,time")
        id_raw, runner_raw, gate_raw, time_raw = fields

        def _blank(v: str) -> bool:
            return v.strip() == ""

        # 全空占位行已在解析阶段跳过；这里部分空即非法。
        if any(_blank(v) for v in fields):
            shown = id_raw.strip() or "（空 ID）"
            raise MergeError(f"CSV 第 {line} 行（ID {shown}）填写不完整，已整批拒绝")

        rid = id_raw.strip()  # ID 沿用现有去空白规则
        runner = _parse_positive_int(runner_raw, f"CSV 第 {line} 行（ID {rid}）的选手编号")
        if gate_raw not in GATES:
            # 门按精确文本比较（页面下拉只会产生 S/M/F）；带空白或其它值即非法。
            raise MergeError(f"CSV 第 {line} 行（ID {rid}）的门必须为 S / M / F 之一")
        time_s = _parse_nonneg_int(time_raw, f"CSV 第 {line} 行（ID {rid}）的秒数")
        parsed.append((line, rid, runner, gate_raw, time_s))
    return parsed


def merge_preview(payload: Any) -> dict:
    """预览合并：当前表非法或 CSV 不满足合并规则时抛 AdjudicationError。

    返回 added（按文件首次出现顺序）/skipped（file=文件内重复、existing=与现有重复）
    及计数；不修改任何输入对象。
    """
    if not isinstance(payload, dict):
        raise MergeError("请求体格式不正确")
    csv_text = payload.get("csv")
    if not isinstance(csv_text, str):
        raise MergeError("缺少上传的 CSV 文本（字段 csv 必须是 UTF-8 字符串）")

    # 当前记录表沿用与裁决完全相同的校验：当前表非法时同样整批拒绝、原表保留。
    runner_ids, _ranges, existing_records = parse_payload(
        {k: v for k, v in payload.items() if k != "csv"}
    )
    known_runners = set(runner_ids)
    rows = parse_device_csv(csv_text)

    existing_by_id = {r.id: r for r in existing_records}
    # ID -> 该 ID 在文件中首次出现的 (行号, 选手, 门, 秒数)；
    # 首次出现即使与现有记录重复也登记，使后续重复按“文件内重复”归类。
    file_first: dict[str, tuple[int, int, str, int]] = {}
    added: list[dict] = []
    skipped: list[dict] = []

    for line, rid, runner, gate, time_s in rows:
        content = (runner, gate, time_s)
        if rid in file_first:
            first_line, fr, fg, ft = file_first[rid]
            if (fr, fg, ft) != content:
                raise MergeError(
                    f"CSV 第 {line} 行与第 {first_line} 行的 ID 相同（{rid}）但内容不一致，"
                    "同 ID 内容冲突，已整批拒绝"
                )
            skipped.append(
                {
                    "id": rid,
                    "runner": runner,
                    "gate": gate,
                    "time": time_s,
                    "line": line,
                    "reason": "file",
                    "duplicateOfLine": first_line,
                }
            )
            continue

        file_first[rid] = (line, runner, gate, time_s)
        cur = existing_by_id.get(rid)
        if cur is not None:
            # 编号、秒数按精确整数比较（parse 已归一），门按精确文本。
            if (cur.runner, cur.gate, cur.time) != content:
                raise MergeError(
                    f"CSV 第 {line} 行的 ID {rid} 与表现有记录内容不一致，"
                    "同 ID 内容冲突，已整批拒绝"
                )
            skipped.append(
                {
                    "id": rid,
                    "runner": runner,
                    "gate": gate,
                    "time": time_s,
                    "line": line,
                    "reason": "existing",
                }
            )
            continue

        if runner not in known_runners:
            raise MergeError(
                f"CSV 第 {line} 行（ID {rid}）的选手编号 {runner} 未登记，"
                "未知选手，已整批拒绝"
            )
        added.append({"id": rid, "runner": runner, "gate": gate, "time": time_s, "line": line})
        total = len(existing_records) + len(added)
        if total > MAX_RECORDS:
            raise MergeError(
                f"合并后感应记录将达到 {total} 条，超过上限 {MAX_RECORDS} 条，已整批拒绝"
            )

    return {
        "existingCount": len(existing_records),
        "addedCount": len(added),
        "skippedCount": len(skipped),
        "totalAfter": len(existing_records) + len(added),
        "added": added,
        "skipped": skipped,
    }


def build_preview(payload: Any) -> dict:
    """预览接口：在合并结果上附加对请求体的指纹令牌。"""
    result = merge_preview(payload)
    result["token"] = fingerprint_token(payload)
    return result


def confirm_merge(payload: Any) -> dict:
    """确认接口：令牌失配（输入/文件已变、非预览所得）一律拒绝并重预览。"""
    if not isinstance(payload, dict):
        raise MergeError("请求体格式不正确")
    token = payload.get("token")
    if not isinstance(token, str) or token == "":
        raise MergeError("缺少预览令牌：请先预览新增 / 重复条目，再确认追加")
    body = {k: v for k, v in payload.items() if k != "token"}
    expected = fingerprint_token(body)
    if not hmac.compare_digest(expected, token):
        raise MergeError("预览已失效：赛道输入或上传文件发生变化，请重新预览后再确认")
    return merge_preview(body)
