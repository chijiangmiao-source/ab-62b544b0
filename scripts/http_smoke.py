#!/usr/bin/env python3
"""对运行中的审计服务做接口 / HTTP 冒烟检验。

依次检验：
1. GET /            页面可访问；
2. GET /healthz     健康路径返回 ok；
3. POST /api/audit  tau 环重命名规程判定为等价，且包含两个初始状态对；
4. POST /api/audit  缺失匹配动作规程判定为不等价，淘汰证据严格按轮次递减；
5. POST /api/audit  无效输入一次返回全部问题（HTTP 400）。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://web:8080").rstrip("/")

TAU_LOOP_EQ = {
    "a": {
        "states": ["S0", "S1", "S2"],
        "initial": "S0",
        "actions": ["alarm", "reset"],
        "transitions": [
            {"id": "a1", "source": "S0", "action": "tau", "target": "S1"},
            {"id": "a2", "source": "S1", "action": "tau", "target": "S0"},
            {"id": "a3", "source": "S1", "action": "alarm", "target": "S2"},
            {"id": "a4", "source": "S2", "action": "reset", "target": "S0"},
        ],
    },
    "b": {
        "states": ["T0", "T1", "T2"],
        "initial": "T0",
        "actions": ["alarm", "reset"],
        "transitions": [
            {"id": "b1", "source": "T0", "action": "tau", "target": "T1"},
            {"id": "b2", "source": "T1", "action": "tau", "target": "T0"},
            {"id": "b3", "source": "T0", "action": "alarm", "target": "T2"},
            {"id": "b4", "source": "T2", "action": "reset", "target": "T0"},
        ],
    },
}

MISSING_ACTION = {
    "a": {
        "states": ["S0", "S1"],
        "initial": "S0",
        "actions": ["alarm"],
        "transitions": [
            {"id": "a1", "source": "S0", "action": "tau", "target": "S1"},
            {"id": "a2", "source": "S1", "action": "alarm", "target": "S0"},
        ],
    },
    "b": {
        "states": ["T0"],
        "initial": "T0",
        "actions": ["alarm"],
        "transitions": [{"id": "b1", "source": "T0", "action": "tau", "target": "T0"}],
    },
}


def check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def get(path: str) -> tuple[int, bytes]:
    req = urllib.request.Request(BASE_URL + path)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def post_audit(spec: dict[str, str]) -> tuple[int, dict]:
    body = json.dumps({k: json.dumps(v) for k, v in spec.items()}).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + "/api/audit",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> int:
    print(f"[smoke] 目标服务 {BASE_URL}")

    print("[smoke] 1. 页面 GET /")
    status, body = get("/")
    check(status == 200, "GET / 返回 200")
    check("弱互模拟".encode() in body, "页面包含弱互模拟审计标题")

    print("[smoke] 2. 健康路径 GET /healthz")
    status, body = get("/healthz")
    check(status == 200, "GET /healthz 返回 200")
    check(json.loads(body)["status"] == "ok", "健康载荷 status=ok")

    print("[smoke] 3. tau 环重命名规程 → 等价")
    status, data = post_audit(TAU_LOOP_EQ)
    check(status == 200, "审计接口返回 200")
    check(data["valid"] is True, "输入有效")
    report = data["report"]
    check(report["equivalent"] is True, "判定两规程等价")
    check(len(report["initial_pairs"]) == 2, "关系中包含两个初始状态对")
    check(all(ip["survives"] for ip in report["initial_pairs"]), "两个初始状态对均存活")

    print("[smoke] 4. 缺失匹配动作规程 → 不等价（证据按轮次递减）")
    status, data = post_audit(MISSING_ACTION)
    check(status == 200, "审计接口返回 200")
    report = data["report"]
    check(report["equivalent"] is False, "判定两规程不等价")
    check(any(not ip["survives"] for ip in report["initial_pairs"]), "初始状态对被淘汰")
    check(report["first_eliminated"]["pair"] == ["S1", "T0"], "首个淘汰对为 (S1,T0)")
    for rd in report["rounds"]:
        for entry in rd["eliminated"]:
            for failure in entry["failures"]:
                for cand in failure["candidates"]:
                    check(
                        cand["eliminated_round"] < rd["round"],
                        f"第 {rd['round']} 轮义务仅引用更早轮次淘汰结果 "
                        f"({cand['pair']} @ R{cand['eliminated_round']})",
                    )

    print("[smoke] 5. 无效输入一次返回全部问题（HTTP 400，清除旧结论）")
    bad_body = json.dumps(
        {
            "a": json.dumps(
                {"states": ["S0", "S0"], "initial": "X", "actions": ["tau"], "transitions": []}
            ),
            "b": "{broken json",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + "/api/audit",
        data=bad_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("无效输入应返回 4xx")
    except urllib.error.HTTPError as exc:
        check(exc.code == 400, "无效输入返回 400")
        payload = json.loads(exc.read().decode("utf-8"))
        check(payload["valid"] is False, "载荷标记 valid=false（旧结论已清除）")
        procs = {p["procedure"] for p in payload["problems"]}
        check(procs == {"A", "B"}, "两侧规程问题一次全部列出")

    print("[smoke] 全部冒烟检验通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError, json.JSONDecodeError) as exc:
        print(f"[smoke] 失败：{exc}", file=sys.stderr)
        sys.exit(1)
