"""静默内部跳转的弱互模拟（weak bisimulation）审计。

算法采用按轮次同步淘汰：

* R0 为两个规程状态的笛卡尔积；
* 第 r 轮开始时仍存活的关系记 R_{r-1}，凡在某方向上存在一条迁移无法在
  R_{r-1} 中找到「静默前缀 + 同动作 + 静默后缀」承接的状态对，于本轮一并
  淘汰；
* 一整轮无淘汰即达到不动点 R∞。

同步语义保证：状态对 (p,q) 于第 r 轮淘汰时，每条失败义务的每个候选状态对
都已在第 1..r-1 轮淘汰（或根本不存在候选），证据因此严格按轮次递减，可自
底向上逐轮复算。

弱迁移定义：``⇒τ`` 为零步或若干步 tau；可观察动作 a 的响应形如
``⇒τ --a--> ⇒τ``。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .lts import LTS, TAU, Transition

Pair = tuple[str, str]


@dataclass(frozen=True)
class Compiled:
    lts: LTS
    outgoing: dict[str, tuple[Transition, ...]]
    # 状态 -> 经弱 tau 步可达的目标 -> 最短见证迁移标识序列（零步为空序列）
    tau_steps: dict[str, dict[str, tuple[str, ...]]]
    # 状态 -> 可观察动作 -> 经 ⇒τ --a--> ⇒τ 可达目标 -> 见证迁移标识序列
    obs_steps: dict[str, dict[str, dict[str, tuple[str, ...]]]]


def compile_lts(lts: LTS) -> Compiled:
    """预计算弱迁移；所有见证路径均为按迁移标识确定的最短路径。"""
    outgoing: dict[str, list[Transition]] = {s: [] for s in lts.states}
    for tr in sorted(lts.transitions, key=lambda t: t.id):
        outgoing[tr.source].append(tr)

    tau_adj: dict[str, list[Transition]] = {s: [] for s in lts.states}
    for tr in lts.transitions:
        if tr.action == TAU:
            tau_adj[tr.source].append(tr)
    for s in tau_adj:
        tau_adj[s].sort(key=lambda t: t.id)

    tau_steps: dict[str, dict[str, tuple[str, ...]]] = {}
    for start in lts.states:
        reached: dict[str, tuple[str, ...]] = {start: ()}
        queue: deque[str] = deque([start])
        while queue:
            u = queue.popleft()
            base = reached[u]
            for tr in tau_adj[u]:  # 已按迁移标识排序，BFS 首个见证即确定
                if tr.target not in reached:
                    reached[tr.target] = base + (tr.id,)
                    queue.append(tr.target)
        tau_steps[start] = reached

    obs_steps: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {}
    for start in lts.states:
        per_action: dict[str, dict[str, tuple[str, ...]]] = {}
        for action in lts.actions:
            reached_o: dict[str, tuple[str, ...]] = {}
            for mid, pre_path in tau_steps[start].items():
                for tr in (t for t in outgoing[mid] if t.action == action):
                    pre = pre_path + (tr.id,)
                    for tgt, post_path in tau_steps[tr.target].items():
                        if tgt not in reached_o:
                            reached_o[tgt] = pre + post_path
            per_action[action] = reached_o
        obs_steps[start] = per_action

    return Compiled(
        lts=lts,
        outgoing={s: tuple(ts) for s, ts in outgoing.items()},
        tau_steps=tau_steps,
        obs_steps=obs_steps,
    )


def _response_targets(comp: Compiled, state: str, action: str) -> dict[str, tuple[str, ...]]:
    """响应侧 state 对动作 action 的全部弱承接目标及见证路径。"""
    if action == TAU:
        return comp.tau_steps[state]
    return comp.obs_steps[state].get(action, {})


def _candidate_pairs(
    pair: Pair, direction: str, tr: Transition, ca: Compiled, cb: Compiled
) -> list[dict[str, object]]:
    """构造一条挑战迁移的全部候选响应状态对（按对端状态稳定排序）。"""
    p, q = pair
    if direction == "A->B":
        targets = _response_targets(cb, q, tr.action)
        result = [
            {"pair": (tr.target, q2), "witness": list(path)}
            for q2, path in targets.items()
        ]
        result.sort(key=lambda c: c["pair"][1])
    else:
        targets = _response_targets(ca, p, tr.action)
        result = [
            {"pair": (p2, tr.target), "witness": list(path)}
            for p2, path in targets.items()
        ]
        result.sort(key=lambda c: c["pair"][0])
    return result


def _failed_obligations(
    pair: Pair, ca: Compiled, cb: Compiled, relation: frozenset[Pair]
) -> list[dict[str, object]]:
    """返回该状态对在给定关系下不成立的全部义务（稳定排序）。"""
    p, q = pair
    failures: list[dict[str, object]] = []
    # A -> B 方向
    for tr in sorted(ca.outgoing[p], key=lambda t: t.id):
        targets = _response_targets(cb, q, tr.action)
        if not any((tr.target, q2) in relation for q2 in targets):
            failures.append(
                {
                    "direction": "A->B",
                    "transition_id": tr.id,
                    "action": tr.action,
                    "challenge": [tr.source, tr.target],
                    "candidates": _candidate_pairs(pair, "A->B", tr, ca, cb),
                }
            )
    # B -> A 方向
    for tr in sorted(cb.outgoing[q], key=lambda t: t.id):
        targets = _response_targets(ca, p, tr.action)
        if not any((p2, tr.target) in relation for p2 in targets):
            failures.append(
                {
                    "direction": "B->A",
                    "action": tr.action,
                    "transition_id": tr.id,
                    "challenge": [tr.source, tr.target],
                    "candidates": _candidate_pairs(pair, "B->A", tr, ca, cb),
                }
            )
    failures.sort(key=lambda f: (f["direction"], f["transition_id"]))
    return failures


def _challenge_action_order(action: str) -> tuple[int, str]:
    # tau 静默挑战在前，可观察动作按字典序
    return (0, "") if action == TAU else (1, action)


def _decorate_failure(
    failure: dict[str, object], eliminated_round: dict[Pair, int]
) -> dict[str, object]:
    """为候选状态对补充淘汰轮次；同步淘汰保证引用轮次严格更早。"""
    candidates = []
    for cand in failure["candidates"]:
        pair: Pair = cand["pair"]
        candidates.append(
            {
                "pair": list(pair),
                "witness": cand["witness"],
                "status": "eliminated",
                "eliminated_round": eliminated_round.get(pair),
            }
        )
    decorated = dict(failure)
    decorated["candidates"] = candidates
    decorated["has_response"] = bool(candidates)
    return decorated


def audit(lts_a: LTS, lts_b: LTS) -> dict[str, object]:
    """执行弱互模拟审计，返回可 JSON 序列化、按轮次组织的完整证据。"""
    ca, cb = compile_lts(lts_a), compile_lts(lts_b)

    all_pairs: list[Pair] = sorted(
        ((p, q) for p in lts_a.states for q in lts_b.states),
        key=lambda x: (x[0], x[1]),
    )
    alive: set[Pair] = set(all_pairs)
    eliminated_round: dict[Pair, int] = {}
    rounds_report: list[dict[str, object]] = []

    r = 0
    while True:
        r += 1
        relation = frozenset(alive)
        drops_detail: list[dict[str, object]] = []
        for pair in sorted(alive, key=lambda x: (x[0], x[1])):
            failures = _failed_obligations(pair, ca, cb, relation)
            if failures:
                drops_detail.append({"pair": pair, "failures": failures})
        if not drops_detail:
            break
        for detail in drops_detail:
            pair = detail["pair"]
            eliminated_round[pair] = r
            alive.remove(pair)
        rounds_report.append(
            {
                "round": r,
                "eliminated": [
                    {
                        "pair": list(detail["pair"]),
                        "failures": [
                            _decorate_failure(f, eliminated_round)
                            for f in detail["failures"]
                        ],
                    }
                    for detail in sorted(drops_detail, key=lambda d: d["pair"])
                ],
            }
        )
    # 跳出循环时 r 为首个无淘汰轮次；淘汰共进行 r-1 轮
    total_rounds = r - 1

    survivors = sorted(alive, key=lambda x: (x[0], x[1]))

    init_pair: Pair = (lts_a.initial, lts_b.initial)
    init_survives = init_pair in alive
    init_round = eliminated_round.get(init_pair)
    initial_pairs = [
        {
            "pair": [lts_a.initial, lts_b.initial],
            "survives": init_survives,
            "eliminated_round": init_round,
        },
        {
            "pair": [lts_b.initial, lts_a.initial],
            "survives": init_survives,
            "eliminated_round": init_round,
        },
    ]

    first_eliminated = None
    if rounds_report:
        first = rounds_report[0]["eliminated"][0]
        challenge_actions = sorted(
            {f["action"] for f in first["failures"]}, key=_challenge_action_order
        )
        first_eliminated = {
            "round": 1,
            "pair": first["pair"],
            "challenge_actions": challenge_actions,
            "failures": first["failures"],
        }

    return {
        "equivalent": init_survives,
        "observable_actions": {
            "A": list(lts_a.actions),
            "B": list(lts_b.actions),
            "union": sorted(set(lts_a.actions) | set(lts_b.actions)),
        },
        "total_rounds": total_rounds,
        "state_pair_count": len(all_pairs),
        "initial_pairs": initial_pairs,
        "first_eliminated": first_eliminated,
        "rounds": rounds_report,
        "survivors": [list(p) for p in survivors],
    }
