"""弱互模拟审计与输入校验的单元测试。"""

from __future__ import annotations

import json
import unittest

from app.bisim import audit, compile_lts
from app.lts import MAX_OBS_ACTIONS, MAX_STATES, parse_lts

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


def _audit_pair(spec: dict[str, str]) -> dict:
    a, pa = parse_lts("A", json.dumps(spec["a"]))
    b, pb = parse_lts("B", json.dumps(spec["b"]))
    assert not pa and not pb
    assert a is not None and b is not None
    return audit(a, b)


class WeakBisimTests(unittest.TestCase):
    def test_tau_loop_renaming_equivalent(self) -> None:
        report = _audit_pair(TAU_LOOP_EQ)
        self.assertTrue(report["equivalent"])
        # 关系中须包含两个初始状态对
        self.assertEqual(len(report["initial_pairs"]), 2)
        for ip in report["initial_pairs"]:
            self.assertTrue(ip["survives"])
            self.assertIsNone(ip["eliminated_round"])
        # 两个初始对都在不动点存活集合中
        survivors = {tuple(p) for p in report["survivors"]}
        self.assertIn(("S0", "T0"), survivors)
        self.assertNotIn(("S0", "T2"), survivors)  # 交叉承诺对仍会被淘汰
        # 最终存活 5 对：{S0,S1}×{T0,T1} 与 (S2,T2)
        self.assertEqual(len(report["survivors"]), 5)

    def test_missing_observable_action_not_equivalent(self) -> None:
        report = _audit_pair(MISSING_ACTION)
        self.assertFalse(report["equivalent"])
        ip0 = report["initial_pairs"][0]
        self.assertEqual(ip0["pair"], ["S0", "T0"])
        self.assertFalse(ip0["survives"])
        # (S1,T0) 无 alarm 候选先于第 1 轮淘汰；初始对经 tau 只能落到它，
        # 故于第 2 轮淘汰——证据按轮次递减
        self.assertEqual(ip0["eliminated_round"], 2)
        fe = report["first_eliminated"]
        self.assertEqual(fe["round"], 1)
        self.assertEqual(fe["pair"], ["S1", "T0"])
        self.assertEqual(fe["challenge_actions"], ["alarm"])
        no_cand = [f for f in fe["failures"] if not f["has_response"]]
        self.assertTrue(no_cand)
        self.assertEqual(no_cand[0]["action"], "alarm")
        # 初始对第 2 轮的失败义务（tau 挑战）唯一候选 (S1,T0) 标记为第 1 轮淘汰
        r2 = report["rounds"][1]["eliminated"][0]
        self.assertEqual(r2["pair"], ["S0", "T0"])
        tau_failures = [f for f in r2["failures"] if f["action"] == "tau"]
        self.assertTrue(tau_failures)
        cand = tau_failures[0]["candidates"][0]
        self.assertEqual(cand["pair"], ["S1", "T0"])
        self.assertEqual(cand["eliminated_round"], 1)

    def test_tau_prefix_action_tau_suffix_matched(self) -> None:
        # A: S0 -a-> S1（直接动作）
        # B: T0 --tau--> T1 -a-> T2 --tau--> T3；静默前缀+动作+静默后缀承接
        spec = {
            "a": {
                "states": ["S0", "S1"],
                "initial": "S0",
                "actions": ["a"],
                "transitions": [{"id": "t", "source": "S0", "action": "a", "target": "S1"}],
            },
            "b": {
                "states": ["T0", "T1", "T2", "T3"],
                "initial": "T0",
                "actions": ["a"],
                "transitions": [
                    {"id": "t1", "source": "T0", "action": "tau", "target": "T1"},
                    {"id": "t2", "source": "T1", "action": "a", "target": "T2"},
                    {"id": "t3", "source": "T2", "action": "tau", "target": "T3"},
                ],
            },
        }
        report = _audit_pair(spec)
        self.assertTrue(report["equivalent"])

    def test_observable_branch_mismatch_detected(self) -> None:
        # A 初始可做 a 与 b；B 仅可做 a：承诺不同
        spec = {
            "a": {
                "states": ["S0", "S1"],
                "initial": "S0",
                "actions": ["a", "b"],
                "transitions": [
                    {"id": "t1", "source": "S0", "action": "a", "target": "S1"},
                    {"id": "t2", "source": "S0", "action": "b", "target": "S1"},
                ],
            },
            "b": {
                "states": ["T0", "T1"],
                "initial": "T0",
                "actions": ["a", "b"],
                "transitions": [
                    {"id": "t1", "source": "T0", "action": "a", "target": "T1"}
                ],
            },
        }
        report = _audit_pair(spec)
        self.assertFalse(report["equivalent"])

    def test_evidence_only_references_earlier_rounds(self) -> None:
        # 构造可产生多轮淘汰的例子：
        # A: S0 -a-> S1；B: T0 -a-> T1, T1 -a-> T0（B 多承诺）且结构错位
        spec = {
            "a": {
                "states": ["S0", "S1", "S2"],
                "initial": "S0",
                "actions": ["a"],
                "transitions": [
                    {"id": "a0", "source": "S0", "action": "a", "target": "S1"},
                    {"id": "a1", "source": "S1", "action": "a", "target": "S2"},
                ],
            },
            "b": {
                "states": ["T0", "T1", "T2"],
                "initial": "T0",
                "actions": ["a"],
                "transitions": [
                    {"id": "b0", "source": "T0", "action": "a", "target": "T1"},
                    {"id": "b1", "source": "T1", "action": "a", "target": "T2"},
                    {"id": "b2", "source": "T2", "action": "a", "target": "T2"},
                ],
            },
        }
        report = _audit_pair(spec)
        self.assertGreaterEqual(report["total_rounds"], 1)
        for rd in report["rounds"]:
            r = rd["round"]
            for entry in rd["eliminated"]:
                for f in entry["failures"]:
                    for cand in f["candidates"]:
                        # 每个候选必须已淘汰，且轮次严格更早
                        self.assertEqual(cand["status"], "eliminated")
                        self.assertIsNotNone(cand["eliminated_round"])
                        self.assertLess(cand["eliminated_round"], r)

    def test_output_is_deterministic(self) -> None:
        r1 = json.dumps(_audit_pair(TAU_LOOP_EQ), ensure_ascii=False, sort_keys=True)
        r2 = json.dumps(_audit_pair(TAU_LOOP_EQ), ensure_ascii=False, sort_keys=True)
        self.assertEqual(r1, r2)

    def test_compiled_weak_moves(self) -> None:
        spec = {
            "states": ["Q0", "Q1", "Q2"],
            "initial": "Q0",
            "actions": ["a"],
            "transitions": [
                {"id": "e1", "source": "Q0", "action": "tau", "target": "Q1"},
                {"id": "e2", "source": "Q1", "action": "a", "target": "Q2"},
                {"id": "e3", "source": "Q2", "action": "tau", "target": "Q0"},
            ],
        }
        lts, problems = parse_lts("A", json.dumps(spec))
        assert not problems and lts is not None
        c = compile_lts(lts)
        # Q0 经零步 tau 可达自身（见证为空）
        self.assertEqual(c.tau_steps["Q0"]["Q0"], ())
        # Q0 经一条 tau 到 Q1
        self.assertEqual(c.tau_steps["Q0"]["Q1"], ("e1",))
        # Q2 沿 tau 环可回到 Q0、再到 Q1
        self.assertEqual(c.tau_steps["Q2"]["Q0"], ("e3",))
        self.assertEqual(c.tau_steps["Q2"]["Q1"], ("e3", "e1"))
        # Q0 可经静默前缀做 a，动作后经 tau 后缀回到 Q0
        self.assertIn("Q2", c.obs_steps["Q0"]["a"])
        self.assertIn("Q0", c.obs_steps["Q0"]["a"])
        # 见证依次为 tau 前缀、动作、tau 后缀
        self.assertEqual(c.obs_steps["Q0"]["a"]["Q0"], ("e1", "e2", "e3"))


class ValidationTests(unittest.TestCase):
    def test_all_problems_reported_at_once(self) -> None:
        bad = {
            "states": ["S0", "S0", 3],
            "initial": "GHOST",
            "actions": ["a", "a", "tau", "b", "c", "d", "e"],
            "transitions": [
                {"id": "x", "source": "NOPE", "action": "zzz", "target": "S1"},
                "not-an-object",
            ],
        }
        lts, problems = parse_lts("A", json.dumps(bad))
        self.assertIsNone(lts)
        messages = {(p.where, p.message) for p in problems}
        # 重复状态、非字符串状态、初始状态缺失、重复动作、tau 保留、动作超限、
        # 迁移端点无效、动作未声明、迁移非对象——全部同时报出
        self.assertTrue(any("states[1]" in w and "重复" in m for w, m in messages))
        self.assertTrue(any("states[2]" in w for w, m in messages))
        self.assertTrue(any(w == "initial" for w, m in messages))
        self.assertTrue(any("actions[1]" in w for w, m in messages))
        self.assertTrue(any("actions[2]" in w and "tau" in m for w, m in messages))
        self.assertTrue(any(w == "actions" and "至多" in m for w, m in messages))
        self.assertTrue(any(".source" in w for w, m in messages))
        self.assertTrue(any(".target" in w for w, m in messages))
        self.assertTrue(any(".action" in w for w, m in messages))
        self.assertTrue(any("transitions[1]" in w for w, m in messages))

    def test_problems_are_stably_sorted(self) -> None:
        bad = {"states": [], "initial": 42, "actions": "nope", "transitions": []}
        _, p1 = parse_lts("A", json.dumps(bad))
        _, p2 = parse_lts("A", json.dumps(bad))
        self.assertEqual([p.as_dict() for p in p1], [p.as_dict() for p in p2])
        where_list = [p.where for p in p1]
        self.assertEqual(where_list, sorted(where_list))

    def test_state_limit(self) -> None:
        bad = {
            "states": [f"S{i}" for i in range(MAX_STATES + 1)],
            "initial": "S0",
            "actions": [],
            "transitions": [],
        }
        _, problems = parse_lts("A", json.dumps(bad))
        self.assertTrue(any("1 到 18" in p.message for p in problems))

    def test_action_limit_and_ascii(self) -> None:
        bad = {
            "states": ["S0"],
            "initial": "S0",
            "actions": ["a", "b", "c", "d", "e"],
            "transitions": [],
        }
        _, problems = parse_lts("A", json.dumps(bad))
        self.assertTrue(any(str(MAX_OBS_ACTIONS) in p.message for p in problems))

        bad2 = {
            "states": ["S0"],
            "initial": "S0",
            "actions": ["a b"],  # 含空白，非可打印 ASCII 单段
            "transitions": [],
        }
        _, problems = parse_lts("A", json.dumps(bad2))
        self.assertTrue(problems)

    def test_duplicate_transition_id(self) -> None:
        bad = {
            "states": ["S0", "S1"],
            "initial": "S0",
            "actions": ["a"],
            "transitions": [
                {"id": "dup", "source": "S0", "action": "a", "target": "S1"},
                {"id": "dup", "source": "S1", "action": "tau", "target": "S0"},
            ],
        }
        _, problems = parse_lts("A", json.dumps(bad))
        self.assertTrue(any("dup" in p.message and "重复" in p.message for p in problems))

    def test_invalid_json(self) -> None:
        lts, problems = parse_lts("A", "{not json")
        self.assertIsNone(lts)
        self.assertEqual(len(problems), 1)
        self.assertIn("JSON", problems[0].message)


if __name__ == "__main__":
    unittest.main()
