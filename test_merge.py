"""设备 CSV 并入：解析、整批拒绝规则、指纹令牌与 HTTP 合同测试。"""

import copy
import sys

sys.path.insert(0, ".")
from fastapi.testclient import TestClient

from app.main import app
from app.adjudication import AdjudicationError
from app.merge import (
    build_preview,
    confirm_merge,
    fingerprint_token,
    parse_device_csv,
)

client = TestClient(app)

failures = 0


def check(cond, msg):
    global failures
    if cond:
        print("  ✓", msg)
    else:
        failures += 1
        print("  ✗", msg)

def expect_error(fn, needle, msg):
    global failures
    try:
        fn()
    except AdjudicationError as exc:
        check(needle in str(exc), f"{msg}（实际：{exc}）")
        return
    failures += 1
    print("  ✗", msg, "—— 未抛出错误")


BASE = {
    "standards": {"smMin": 10, "smMax": 60, "mfMin": 10, "mfMax": 60},
    "runners": [{"number": 101}, {"number": 102}],
    "records": [
        {"id": "a1", "runner": 101, "gate": "S", "time": 0},
        {"id": "b1", "runner": 101, "gate": "M", "time": 12},
        {"id": "c1", "runner": 101, "gate": "F", "time": 22},
    ],
}


def body(csv_text, **over):
    d = copy.deepcopy(BASE)
    d.update(over)
    d["csv"] = csv_text
    return d


print("解析：表头 / 引号 / 空行 / BOM")
rows = parse_device_csv("id,runner,gate,time\nx1,101,S,3\n")
check(rows == [(2, "x1", 101, "S", 3)], "基本解析含物理行号")

rows = parse_device_csv(
    'id,runner,gate,time\n"p,1",101,S,3\n"q""2",102,M,5\n"r\n3",101,F,9\n'
)
check(
    [(r[1], r[2], r[4]) for r in rows] == [("p,1", 101, 3), ('q"2', 102, 5), ("r\n3", 101, 9)],
    "支持引号内逗号、双引号转义与换行",
)
check(rows[2][0] == 5, "跨行引号字段记结束物理行号")

rows = parse_device_csv("﻿id,runner,gate,time\nx1,101,S,3\n")
check(rows[0][1] == "x1", "接受 UTF-8 BOM")

rows = parse_device_csv("id,runner,gate,time\r\nx1,101,S,3\r\n")
check(rows == [(2, "x1", 101, "S", 3)], "接受 CRLF")

rows = parse_device_csv("id,runner,gate,time\n\n   \nx1,101,S,3\n\n,,,,\n")
check(rows == [(4, "x1", 101, "S", 3)], "物理空行与全空占位行跳过且保留行号")

expect_error(lambda: parse_device_csv(""), "表头", "空文件")
expect_error(lambda: parse_device_csv("\n\n"), "表头", "只有空行")
expect_error(lambda: parse_device_csv("id,runner,gate\nx1,101,S\n"), "表头", "表头三列")
expect_error(lambda: parse_device_csv("ID,runner,gate,time\nx1,101,S,1\n"), "表头", "表头大小写不符")
expect_error(lambda: parse_device_csv("id,runner,gate,time \nx1,101,S,1\n"), "表头", "表头多余空白")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,101,S\n'), "4 列", "数据行列数不足")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,101,S,1,9\n'), "4 列", "数据行列数过多")
expect_error(lambda: parse_device_csv('id,runner,gate,time\n,101,S,1\n'), "不完整", "ID 空")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,,S,1\n'), "不完整", "编号空")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,101,,1\n'), "不完整", "门空")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,101,S,\n'), "不完整", "秒数空")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,0,S,1\n'), "正整数", "编号 0")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,1.0,S,1\n'), "整数", "编号为小数")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,101,X,1\n'), "S / M / F", "非法门")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,101,S,-1\n'), "非负整数", "负秒数")
expect_error(lambda: parse_device_csv('id,runner,gate,time\nx1,101,S,1.5\n'), "非负整数", "小数秒数")
# 未闭合引号到文件尾：最后一条记录列数不对，同样按非法行整批拒绝。
expect_error(lambda: parse_device_csv('id,runner,gate,time\n"x1,101,S,3\n'), "4 列", "未闭合引号")

print("合并：新增 / 重复 / 顺序")
r = build_preview(body("id,runner,gate,time\nn1,102,S,30\nn2,102,M,40\n"))
check(
    [a["id"] for a in r["added"]] == ["n1", "n2"]
    and r["addedCount"] == 2
    and r["skippedCount"] == 0
    and r["existingCount"] == 3
    and r["totalAfter"] == 5,
    "新增按文件顺序，计数正确",
)
check(len(r["token"]) == 64, "预览返回 SHA-256 指纹令牌")

r = build_preview(body("id,runner,gate,time\na1,101,S,0\nb1,101,M,12\nc1,101,F,22\n"))
check(
    [s["reason"] for s in r["skipped"]] == ["existing"] * 3 and r["addedCount"] == 0,
    "与现有记录一致全部 existing 跳过",
)

r = build_preview(body("id,runner,gate,time\nn1,102,S,1\nn1,102,S,1\n"))
check(
    r["addedCount"] == 1
    and len(r["skipped"]) == 1
    and r["skipped"][0]["reason"] == "file"
    and r["skipped"][0]["duplicateOfLine"] == 2,
    "文件内重复标 file 并指向首次出现行",
)

print("精确整数比较：前导零等值归一")
r = build_preview(body("id,runner,gate,time\na1,0101,S,000\n"))
check(r["skipped"][0]["reason"] == "existing", "编号/秒数前导零按数值相等判重")

print("整批拒绝")
expect_error(
    lambda: build_preview(body("id,runner,gate,time\na1,101,F,0\n")),
    "内容不一致",
    "同 ID 与现有冲突",
)
expect_error(
    lambda: build_preview(body("id,runner,gate,time\na1,0101,S,1\n")),
    "内容不一致",
    "秒数差 1（前导零写法不掩盖冲突）",
)
expect_error(
    lambda: build_preview(body("id,runner,gate,time\na1,102,S,0\n")),
    "内容不一致",
    "编号冲突",
)
expect_error(
    lambda: build_preview(body("id,runner,gate,time\nd1,101,S,1\nd1,101,F,1\n")),
    "内容不一致",
    "文件内同 ID 冲突",
)
expect_error(
    lambda: build_preview(body("id,runner,gate,time\nz9,103,S,1\n")),
    "未登记",
    "未知选手",
)
expect_error(
    lambda: build_preview(
        body("id,runner,gate,time\nz9,101,S,1\n",
             records=[{"id": "z", "runner": 101, "gate": "S"}])
    ),
    "填写不完整",
    "当前表非法（记录部分空）同样整批拒绝",
)

# 合并后超过 40：现有 3 条，再加 38 条新增 -> 41
csv40 = "id,runner,gate,time\n" + "\n".join(f"n{i:02d},102,S,{i}" for i in range(38)) + "\n"
expect_error(lambda: build_preview(body(csv40)), "41", "合并后 41 条整批拒绝")
# 37 条新增 -> 40 条可以
csv37 = "id,runner,gate,time\n" + "\n".join(f"n{i:02d},102,S,{i}" for i in range(37)) + "\n"
check(build_preview(body(csv37))["totalAfter"] == 40, "合并后恰好 40 条允许")
# 文件内重复不占用 40 限额：40 条新增 ID 中重复若干
lines = [f"n{i:02d},102,S,{i}" for i in range(37)] + ["n00,102,S,0", "n00,102,S,0"]
check(build_preview(body("id,runner,gate,time\n" + "\n".join(lines) + "\n"))["totalAfter"] == 40,
      "文件内重复不计入合并后条数")

print("确认：指纹令牌")
b = body("id,runner,gate,time\nn1,102,S,30\n")
prev = build_preview(b)
tok = prev["token"]
r = confirm_merge({**b, "token": tok})
check(r["addedCount"] == 1 and "token" not in r, "同体确认成功且响应不带令牌")
check(fingerprint_token({**b, "token": tok}) != tok, "令牌不随 token 字段变化（确认按去 token 体重算）")
expect_error(lambda: confirm_merge({**b}), "预览令牌", "缺令牌拒绝")
expect_error(lambda: confirm_merge({**b, "token": "x" * 64}), "预览已失效", "伪造令牌拒绝")
# 输入变化：改区间
changed = {**b, "standards": {**b["standards"], "smMin": 11}}
expect_error(lambda: confirm_merge({**changed, "token": tok}), "预览已失效", "赛道输入变化令牌失配")
# 文件变化
changed_csv = body("id,runner,gate,time\nn1,102,S,31\n")
expect_error(lambda: confirm_merge({**changed_csv, "token": tok}), "预览已失效", "文件内容变化令牌失配")
# 键序/空白不影响指纹：直接对 dict 重排构造
reordered = {"csv": b["csv"], "runners": b["runners"], "records": b["records"], "standards": b["standards"]}
check(fingerprint_token(reordered) == tok, "指纹与键顺序无关")

print("HTTP 合同")
resp = client.post("/api/merge/preview", json=b)
check(resp.status_code == 200 and resp.json()["ok"] and "token" in resp.json()["result"], "预览 200")
resp = client.post("/api/merge/confirm", json={**b, "token": tok})
check(resp.status_code == 200 and resp.json()["ok"], "确认 200")
resp = client.post("/api/merge/confirm", json={**changed, "token": tok})
check(resp.status_code == 422 and not resp.json()["ok"] and "预览已失效" in resp.json()["error"], "失配 422")
resp = client.post("/api/merge/preview", json=body("id,runner,gate,time\nz9,103,S,1\n"))
check(resp.status_code == 422 and "未登记" in resp.json()["error"], "未知选手 422 中文原因")
resp = client.post("/api/merge/preview", content=b"xxx", headers={"content-type": "application/json"})
check(resp.status_code == 400 and resp.json()["ok"] is False, "非 JSON 400")
resp = client.post("/api/merge/preview", json={"standards": {}, "runners": [], "records": []})
check(resp.status_code == 422, "缺 csv 随当前表校验一起 422")

# 既有合同不受影响
resp = client.post(
    "/api/adjudicate",
    json={**{k: v for k, v in BASE.items()}, "records": BASE["records"]},
)
check(resp.status_code == 200 and resp.json()["result"]["runners"][0]["lapCount"] == 1, "/api/adjudicate 合同不变")
resp = client.post("/api/diagnose", json={**{k: v for k, v in BASE.items()}, "records": BASE["records"]})
check(resp.status_code == 200 and "diagnoses" in resp.json()["result"]["runners"][0], "/api/diagnose 合同不变")

print(f"\n{'ALL MERGE TESTS PASSED' if failures == 0 else f'{failures} ASSERTION(S) FAILED'}")
sys.exit(1 if failures else 0)
