"""赛道计次裁决核心算法。

规则摘要：
- 每趟从同一选手的感应记录中选 起点(S) -> 中途门(M) -> 终点(F) 各一条，
  两段耗时（M-S、F-M）均须落在给定的正整数闭区间内。
- 同一选手的不同趟不得共用记录；后一趟起点时间须严格晚于前一趟终点时间。
- 最优化目标（按优先级）：
    1) 完整计次数最多；
    2) 各趟用时（终点秒数 - 起点秒数）之和最短；
    3) 按计次时间顺序串接的记录 ID 序列字典序最小。

实现方式：枚举所有合法“趟”，按终点时间排序后在 DAG 上做最长路 DP；
前驱用二分查找（要求前驱终点 < 当前起点），比较键直接使用
(趟数, 总用时, ID 序列) 的三元组（趟数越大越好、其余越小越好）。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

GATES = ("S", "M", "F")


class AdjudicationError(ValueError):
    """输入非法错误，message 为可直接展示的中文说明。"""


MAX_RUNNERS = 3
MAX_RECORDS = 40


def _is_uint_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != "" and all(
        c in "0123456789" for c in value.strip()
    )


def _parse_nonneg_int(value: Any, field: str) -> int:
    # 拒绝 bool（bool 是 int 的子类），只接受十进制整数字面量，杜绝 1.0 / "0x1" 等。
    if isinstance(value, bool):
        raise AdjudicationError(f"{field}必须是非负整数")
    if isinstance(value, int):
        n = value
    elif _is_uint_text(value):
        n = int(value.strip())
    else:
        raise AdjudicationError(f"{field}必须是非负整数")
    if n < 0:
        raise AdjudicationError(f"{field}必须是非负整数")
    return n


def _parse_positive_int(value: Any, field: str) -> int:
    n = _parse_nonneg_int(value, field)
    if n <= 0:
        raise AdjudicationError(f"{field}必须是正整数")
    return n


def _require_object(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise AdjudicationError(f"{field}格式不正确")
    return value


def _require_list(value: Any, field: str) -> list:
    if not isinstance(value, list):
        raise AdjudicationError(f"{field}格式不正确")
    return value


def _parse_closed_range(standards: dict, lo_key: str, hi_key: str, label: str) -> tuple[int, int]:
    lo = _parse_positive_int(standards.get(lo_key), f"{label}区间下限")
    hi = _parse_positive_int(standards.get(hi_key), f"{label}区间上限")
    if lo > hi:
        raise AdjudicationError(f"{label}区间下限不能大于上限")
    return lo, hi


@dataclass(frozen=True)
class Record:
    id: str
    runner: int
    gate: str
    time: int


def parse_payload(payload: Any) -> tuple[list[int], dict[str, tuple[int, int]], list[Record]]:
    """校验并解析请求体，非法时抛出 AdjudicationError。

    返回 (按输入顺序排列的选手编号列表, 两段区间, 按输入顺序排列的记录列表)。
    """
    root = _require_object(payload, "请求体")

    standards = _require_object(root.get("standards"), "合格耗时区间")
    sm_range = _parse_closed_range(standards, "smMin", "smMax", "起点至中途门")
    mf_range = _parse_closed_range(standards, "mfMin", "mfMax", "中途门至终点")

    runner_ids = parse_runners(root.get("runners"))
    records = parse_records(root.get("records"), set(runner_ids))
    ranges = {"SM": sm_range, "MF": mf_range}
    return runner_ids, ranges, records


def parse_runners(runner_rows: Any) -> list[int]:
    """解析选手列表（1～3 名、正整数、互异），按输入顺序返回编号。

    供裁决/诊断与设备日志合并预览共用同一套校验。
    """
    runner_rows = _require_list(runner_rows, "选手列表")
    if not 1 <= len(runner_rows) <= MAX_RUNNERS:
        raise AdjudicationError(f"选手人数必须为 1～{MAX_RUNNERS} 名")

    runner_ids: list[int] = []
    seen_runner: set[int] = set()
    for i, row in enumerate(runner_rows, 1):
        row = _require_object(row, f"第 {i} 名选手")
        number_raw = row.get("number")
        if number_raw is None or (isinstance(number_raw, str) and number_raw.strip() == ""):
            # 前端只发送有内容的选手行；收到空编号一律视为非法，防止静默漏录。
            raise AdjudicationError(f"第 {i} 名选手编号不能为空")
        rid = _parse_positive_int(number_raw, f"第 {i} 名选手编号")
        if rid in seen_runner:
            raise AdjudicationError(f"选手编号 {rid} 重复")
        seen_runner.add(rid)
        runner_ids.append(rid)
    return runner_ids


def parse_records(record_rows: Any, valid_runners: set[int]) -> list[Record]:
    """解析感应记录列表（至多 40 条、ID 唯一、选手已登记、门合法、秒数非负整数）。

    四个字段全空的占位行会被跳过；部分空或任何字段非法均抛出 AdjudicationError。
    供裁决/诊断与设备日志合并预览共用同一套校验。
    """
    record_rows = _require_list(record_rows, "感应记录列表")
    if len(record_rows) > MAX_RECORDS:
        raise AdjudicationError(f"感应记录至多 {MAX_RECORDS} 条")

    records: list[Record] = []
    seen_ids: set[str] = set()
    for i, row in enumerate(record_rows, 1):
        row = _require_object(row, f"第 {i} 条感应记录")

        # 四个字段全空视为未填写的占位行，直接跳过；部分空则按非法处理。
        rid_raw = row.get("id")
        runner_raw = row.get("runner")
        gate_raw = row.get("gate")
        time_raw = row.get("time")

        def _blank(v: Any) -> bool:
            return v is None or (isinstance(v, str) and v.strip() == "")

        if all(_blank(v) for v in (rid_raw, runner_raw, gate_raw, time_raw)):
            continue
        if any(_blank(v) for v in (rid_raw, runner_raw, gate_raw, time_raw)):
            raise AdjudicationError(f"第 {i} 条感应记录填写不完整")

        if not isinstance(rid_raw, str):
            raise AdjudicationError(f"第 {i} 条感应记录的 ID 必须是字符串")
        rid = rid_raw.strip()
        if rid in seen_ids:
            raise AdjudicationError(f"感应记录 ID 重复：{rid}")
        seen_ids.add(rid)

        runner = _parse_positive_int(runner_raw, f"记录 {rid} 的选手编号")
        if runner not in valid_runners:
            raise AdjudicationError(f"记录 {rid} 的选手编号 {runner} 未登记")

        if gate_raw not in GATES:
            raise AdjudicationError(f"记录 {rid} 的门必须为 S / M / F 之一")

        time_s = _parse_nonneg_int(time_raw, f"记录 {rid} 的秒数")
        records.append(Record(id=rid, runner=runner, gate=gate_raw, time=time_s))
    return records


@dataclass(frozen=True)
class Trip:
    """一躺候选：三条记录及两段耗时、总用时。"""

    s: Record
    m: Record
    f: Record
    sm: int
    mf: int

    @property
    def total(self) -> int:
        return self.f.time - self.s.time

    @property
    def ids(self) -> tuple[str, ...]:
        return (self.s.id, self.m.id, self.f.id)


def _build_trips(
    runner_records: list[Record], ranges: dict[str, tuple[int, int]]
) -> list[Trip]:
    by_gate: dict[str, list[Record]] = defaultdict(list)
    for rec in runner_records:
        by_gate[rec.gate].append(rec)
    sm_lo, sm_hi = ranges["SM"]
    mf_lo, mf_hi = ranges["MF"]

    trips: list[Trip] = []
    # M 在内层，可按时间顺序做范围裁剪；数据量极小，直接全枚举也足够。
    for s in by_gate["S"]:
        for m in by_gate["M"]:
            sm = m.time - s.time
            if not (sm_lo <= sm <= sm_hi):
                continue
            for f in by_gate["F"]:
                mf = f.time - m.time
                if mf_lo <= mf <= mf_hi:
                    trips.append(Trip(s=s, m=m, f=f, sm=sm, mf=mf))

    # 以终点时间为主序，保证 DP 中前驱必然先出现；其余键保证结果确定。
    trips.sort(
        key=lambda t: (
            t.f.time,
            t.s.time,
            t.m.time,
            t.total,
            t.s.id,
            t.m.id,
            t.f.id,
        )
    )
    return trips


# 每个 DP 状态：(趟数, 总用时, ID 串接元组)
# 趟数越大越好；总用时、ID 序列越小越好。
Key = tuple[int, int, tuple[str, ...]]

EMPTY_KEY: Key = (0, 0, ())


def _key_better(cand: Key, best: Key | None) -> bool:
    """cand 是否优于（或等于）当前最优 best。"""
    if best is None:
        return True
    if cand[0] != best[0]:
        return cand[0] > best[0]
    if cand[1] != best[1]:
        return cand[1] < best[1]
    return cand[2] < best[2]


def _best_plan(trips: list[Trip]) -> tuple[Key, set[str], list[Trip]]:
    """在按终点时间排序的合法趟 DAG 上求最优完整方案。

    返回 (最优键, 采纳记录 ID 集合, 按时间顺序排列的采纳趟)。
    与裁决输出完全一致：趟数最多 → 总用时最短 → ID 串接序列字典序最小。
    """
    finish_times = [t.f.time for t in trips]

    # prev[i]：以 trips[i] 结尾的最优链中，上一趟的下标（-1 为首趟）。
    prev: list[int] = []
    # 前缀最优（按 trips 顺序）及其下标，供二分跳转使用。
    prefix_best: list[Key] = []
    prefix_idx: list[int] = []

    for i, trip in enumerate(trips):
        base: Key = (1, trip.total, trip.ids)
        best: Key = base
        best_prev = -1

        # 只允许终点严格早于本趟起点的趟作为前驱。
        j = bisect_right(finish_times, trip.s.time - 1, 0, i) - 1
        if j >= 0:
            pk = prefix_best[j]
            cand: Key = (
                pk[0] + 1,
                pk[1] + trip.total,
                pk[2] + trip.ids,
            )
            if _key_better(cand, best):
                best = cand
                best_prev = prefix_idx[j]

        prev.append(best_prev)

        if i == 0:
            prefix_best.append(best)
            prefix_idx.append(i)
        else:
            pb = prefix_best[-1]
            if _key_better(best, pb):
                prefix_best.append(best)
                prefix_idx.append(i)
            else:
                prefix_best.append(pb)
                prefix_idx.append(prefix_idx[-1])

    used: set[str] = set()
    chosen: list[Trip] = []
    if trips:
        cur = prefix_idx[-1]
        while cur != -1:
            chosen.append(trips[cur])
            cur = prev[cur]
        chosen.reverse()
        for trip in chosen:
            used.update(trip.ids)

    return (prefix_best[-1] if trips else EMPTY_KEY), used, chosen


def _suffix_best_by_start(
    trips_f: list[Trip], start_order: list[int]
) -> list[Key]:
    """后缀最优键：suffix[k] 为只使用 start_order[k:] 中趟时的最优完整链键。

    start_order 给出按起点时间升序（同秒按确定性次序）排列的 trips_f 下标。
    suffix[k] 对应链的首趟起点必然 >= start_times[k]：对目标趟 T，取
    q = bisect_left(start_times, T.f.time + 1) 后拼接 suffix[q]，即可保证
    后缀首趟起点严格晚于 T 的终点。链内趟与趟按时间严格分离（趟内 s<m<f、
    相邻趟 s>前驱 f），因此同链趟之间不可能共用记录，前缀/后缀可独立求最优后
    与目标趟直接拼接。
    """
    n = len(trips_f)
    start_times = [trips_f[i].s.time for i in start_order]
    suffix: list[Key] = [EMPTY_KEY] * n

    for k in range(n - 1, -1, -1):
        i = start_order[k]
        trip = trips_f[i]
        take: Key = (1, trip.total, trip.ids)
        # 后继首趟起点必须严格晚于本趟终点：按起点时间二分，而非按下标 +1
        # （同一秒存在多条趟时，下标 +1 取到的仍是同秒趟，会错误共用记录）。
        q = bisect_left(start_times, trip.f.time + 1, k + 1, n)
        if q < n:
            sk = suffix[q]
            if sk[0] > 0:
                cand: Key = (sk[0] + 1, trip.total + sk[1], trip.ids + sk[2])
                if _key_better(cand, take):
                    take = cand

        # 后缀最优（允许跳过本趟）：在 take 与 suffix[k+1] 间取优。
        if k == n - 1 or _key_better(take, suffix[k + 1]):
            suffix[k] = take
        else:
            suffix[k] = suffix[k + 1]
    return suffix


def _prefix_best_keys(trips_f: list[Trip]) -> tuple[list[Key], list[int]]:
    """终点排序数组的逐前缀最优链键与对应终点时间：prefix_best[i] 为只用 trips[0..i] 时的最优。"""
    finish_times = [t.f.time for t in trips_f]
    prefix_best: list[Key] = []
    for i, trip in enumerate(trips_f):
        best: Key = (1, trip.total, trip.ids)
        p = bisect_right(finish_times, trip.s.time - 1, 0, i) - 1
        if p >= 0:
            pk = prefix_best[p]
            cand = (pk[0] + 1, pk[1] + trip.total, pk[2] + trip.ids)
            if _key_better(cand, best):
                best = cand
        if i == 0:
            prefix_best.append(best)
        elif _key_better(best, prefix_best[-1]):
            prefix_best.append(best)
        else:
            prefix_best.append(prefix_best[-1])
    return prefix_best, finish_times


def _best_through_trip(
    trip_i: int,
    trips_f: list[Trip],
    start_times: list[int],
    finish_times: list[int],
    prefix_best: list[Key],
    suffix: list[Key],
) -> Key:
    """包含 trips_f[trip_i] 这一趟的最优完整链键：最优前缀 + 该趟 + 最优后缀。"""
    trip = trips_f[trip_i]

    # 前缀：终点严格早于本趟起点（终点序前缀最优即保证整链与目标趟时间分离）。
    p = bisect_right(finish_times, trip.s.time - 1, 0, trip_i) - 1
    pre: Key = prefix_best[p] if p >= 0 else EMPTY_KEY

    # 后缀：首趟起点严格晚于本趟终点（按起点时间二分）。
    q = bisect_left(start_times, trip.f.time + 1)
    post: Key = suffix[q] if q < len(trips_f) else EMPTY_KEY

    return (pre[0] + 1 + post[0], pre[1] + trip.total + post[1], pre[2] + trip.ids + post[2])


# 替代性诊断三档分类（值为稳定的英文代码，中文标签由页面呈现）：
# equal    同优可替代：存在包含目标记录、计次数与原总用时都与现方案相同的合法完整方案
# inferior 仅次优可用：至少属于一条合格趟，但任何完整方案都达不到现方案的计次数/总用时
# unusable 无法成趟：该选手的任何一条合格趟都不包含该记录
DIAG_EQUAL = "equal"
DIAG_INFERIOR = "inferior"
DIAG_UNUSABLE = "unusable"


def _diagnose_one(
    runner: int, records: list[Record], ranges: dict[str, tuple[int, int]]
) -> dict:
    mine = [r for r in records if r.runner == runner]
    trips = _build_trips(mine, ranges)

    best_key, used, _chosen = _best_plan(trips)
    target_count, target_total = best_key[0], best_key[1]

    # 起点升序的趟次序（同秒时沿用与终点排序一致的确定性键，保证结果稳定）。
    start_order = sorted(
        range(len(trips)),
        key=lambda i: (
            trips[i].s.time,
            trips[i].f.time,
            trips[i].m.time,
            trips[i].total,
            trips[i].s.id,
            trips[i].m.id,
            trips[i].f.id,
        ),
    )
    suffix = _suffix_best_by_start(trips, start_order)
    start_times = [trips[i].s.time for i in start_order]
    prefix_best, finish_times = _prefix_best_keys(trips)

    # 每条记录 -> 包含它的最优完整链键（遍历所有包含该记录的合格趟取最优）。
    best_with: dict[str, Key] = {}
    for i, trip in enumerate(trips):
        key = _best_through_trip(
            i, trips, start_times, finish_times, prefix_best, suffix
        )
        for rid in trip.ids:
            cur = best_with.get(rid)
            if cur is None or _key_better(key, cur):
                best_with[rid] = key

    def classify(rec_id: str) -> tuple[str, Key | None]:
        key = best_with.get(rec_id)
        if key is None:
            return DIAG_UNUSABLE, None
        if key[0] == target_count and key[1] == target_total:
            return DIAG_EQUAL, key
        return DIAG_INFERIOR, key

    items: list[dict] = []
    for rec in mine:
        if rec.id in used:
            continue  # 只诊断未采纳记录
        cls, key = classify(rec.id)
        item = {"id": rec.id, "gate": rec.gate, "time": rec.time, "classification": cls}
        if cls == DIAG_EQUAL:
            assert key is not None
            item["alternative"] = {
                "lapCount": key[0],
                "totalTime": key[1],
                "recordIds": list(key[2]),
            }
        items.append(item)

    return {
        "runner": runner,
        "lapCount": target_count,
        "totalTime": target_total,
        "diagnoses": items,
    }


def _adjudicate_one(
    runner: int, records: list[Record], ranges: dict[str, tuple[int, int]]
) -> dict:
    mine = [r for r in records if r.runner == runner]
    trips = _build_trips(mine, ranges)

    _best_key, used, chosen = _best_plan(trips)

    laps_out = [
        {
            "start": {"id": t.s.id, "time": t.s.time},
            "middle": {"id": t.m.id, "time": t.m.time},
            "finish": {"id": t.f.id, "time": t.f.time},
            "smTime": t.sm,
            "mfTime": t.mf,
            "totalTime": t.total,
        }
        for t in chosen
    ]
    unused = [
        {"id": r.id, "gate": r.gate, "time": r.time}
        for r in mine
        if r.id not in used
    ]
    return {
        "runner": runner,
        "lapCount": len(chosen),
        "totalTime": sum((t.total for t in chosen), 0),
        "laps": laps_out,
        "unused": unused,
    }


def adjudicate(payload: Any) -> dict:
    """裁决入口：非法输入抛 AdjudicationError；合法时返回可序列化结果。"""
    runner_ids, ranges, records = parse_payload(payload)
    return {
        "runners": [_adjudicate_one(rid, records, ranges) for rid in runner_ids]
    }


def diagnose(payload: Any) -> dict:
    """替代性诊断入口：输入与裁决完全相同（同一套校验），只诊断未采纳记录。

    对每名选手返回按录入顺序排列的未采纳记录分类：
    - equal：存在包含该记录、且计次数与原总用时都与现方案相同的合法完整方案，
      同时给出该套方案按计次时间顺序串接的完整记录 ID 序列（同优多方案取字典序最小）；
    - inferior：该记录至少属于一条合格趟，但任何完整方案都达不到现方案；
    - unusable：该选手的任何合格趟都不包含它。
    同优判定只比 (计次数, 原总用时)，不考虑 ID 决胜与罚秒。
    """
    runner_ids, ranges, records = parse_payload(payload)
    return {
        "runners": [_diagnose_one(rid, records, ranges) for rid in runner_ids]
    }
