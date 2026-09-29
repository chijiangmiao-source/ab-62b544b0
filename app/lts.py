"""有限状态规程（LTS）的数据模型与一次性全量校验。

两个规程各自拥有：有限状态集合、唯一初始状态、可观察动作集合（至多四种
ASCII 动作）以及若干带唯一标识的迁移；迁移动作既可以是已声明的可观察动作，
也可以是静默动作 ``tau``。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

TAU = "tau"
MAX_STATES = 18
MAX_OBS_ACTIONS = 4
MAX_IDENT_LEN = 32
MAX_ACTION_LEN = 8

IDENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,%d}$" % MAX_IDENT_LEN)
ACTION_RE = re.compile(r"^[!-~]{1,%d}$" % MAX_ACTION_LEN)  # 非空白可打印 ASCII


@dataclass(frozen=True)
class Transition:
    id: str
    source: str
    action: str
    target: str


@dataclass(frozen=True)
class LTS:
    label: str
    states: tuple[str, ...]
    initial: str
    actions: tuple[str, ...]  # 仅可观察动作，按声明次序
    transitions: tuple[Transition, ...]


@dataclass(frozen=True)
class Problem:
    procedure: str  # "A" / "B"
    where: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"procedure": self.procedure, "where": self.where, "message": self.message}


def _err(problems: list[Problem], procedure: str, where: str, message: str) -> None:
    problems.append(Problem(procedure, where, message))


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def parse_lts(label: str, raw_text: str) -> tuple[LTS | None, list[Problem]]:
    """解析单个规程的 JSON 文本，返回 (规程或 None, 全部问题)。

    问题按 (位置, 描述) 稳定排序，绝不因首个错误短路。
    """
    problems: list[Problem] = []
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        _err(problems, label, "$", f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行第 {exc.colno} 列）")
        return None, sorted(problems, key=lambda p: (p.where, p.message))
    if not isinstance(data, dict):
        _err(problems, label, "$", "规程必须是 JSON 对象")
        return None, sorted(problems, key=lambda p: (p.where, p.message))

    # ---- 状态 --------------------------------------------------------------
    states_raw = data.get("states")
    states: list[str] = []
    if not isinstance(states_raw, list):
        _err(problems, label, "states", "states 必须是数组")
    else:
        if not (1 <= len(states_raw) <= MAX_STATES):
            _err(
                problems,
                label,
                "states",
                f"状态数量必须在 1 到 {MAX_STATES} 之间，当前为 {len(states_raw)}",
            )
        seen: set[str] = set()
        for idx, item in enumerate(states_raw):
            where = f"states[{idx}]"
            if not _is_str(item):
                _err(problems, label, where, "状态标识必须是字符串")
                continue
            if not IDENT_RE.match(item):
                _err(
                    problems,
                    label,
                    where,
                    f"状态标识 {item!r} 非法：仅限 1-{MAX_IDENT_LEN} 位字母、数字、下划线或连字符",
                )
                continue
            if item in seen:
                _err(problems, label, where, f"状态标识 {item!r} 重复")
            seen.add(item)
            states.append(item)

    state_set = set(states)

    # ---- 初始状态 ----------------------------------------------------------
    initial_raw = data.get("initial")
    initial = ""
    if not _is_str(initial_raw):
        _err(problems, label, "initial", "initial 必须是字符串")
    else:
        initial = initial_raw
        if initial not in state_set:
            _err(problems, label, "initial", f"初始状态 {initial!r} 不在 states 中")

    # ---- 可观察动作 --------------------------------------------------------
    actions_raw = data.get("actions")
    actions: list[str] = []
    if not isinstance(actions_raw, list):
        _err(problems, label, "actions", "actions 必须是数组")
    else:
        if len(actions_raw) > MAX_OBS_ACTIONS:
            _err(
                problems,
                label,
                "actions",
                f"可观察动作至多 {MAX_OBS_ACTIONS} 种，当前为 {len(actions_raw)} 种",
            )
        seen_actions: set[str] = set()
        for idx, item in enumerate(actions_raw):
            where = f"actions[{idx}]"
            if not _is_str(item):
                _err(problems, label, where, "动作必须是字符串")
                continue
            if not ACTION_RE.match(item):
                _err(
                    problems,
                    label,
                    where,
                    f"动作 {item!r} 非法：仅限 1-{MAX_ACTION_LEN} 个非空白可打印 ASCII 字符",
                )
                continue
            if item == TAU:
                _err(problems, label, where, f"{TAU!r} 是保留的静默动作，不能声明为可观察动作")
                continue
            if item in seen_actions:
                _err(problems, label, where, f"动作 {item!r} 重复")
            seen_actions.add(item)
            actions.append(item)

    action_set = set(actions)

    # ---- 迁移 --------------------------------------------------------------
    transitions_raw = data.get("transitions")
    transitions: list[Transition] = []
    if not isinstance(transitions_raw, list):
        _err(problems, label, "transitions", "transitions 必须是数组")
    else:
        seen_tids: set[str] = set()
        for idx, item in enumerate(transitions_raw):
            where = f"transitions[{idx}]"
            if not isinstance(item, dict):
                _err(problems, label, where, "迁移必须是 JSON 对象")
                continue
            tid = item.get("id")
            source = item.get("source")
            action = item.get("action")
            target = item.get("target")
            ok = True
            for field, value in (("id", tid), ("source", source), ("action", action), ("target", target)):
                if not _is_str(value):
                    _err(problems, label, f"{where}.{field}", f"迁移字段 {field} 必须是非空字符串")
                    ok = False
            if not ok:
                continue
            assert isinstance(tid, str) and isinstance(source, str)
            assert isinstance(action, str) and isinstance(target, str)
            if not IDENT_RE.match(tid):
                _err(
                    problems,
                    label,
                    f"{where}.id",
                    f"迁移标识 {tid!r} 非法：仅限 1-{MAX_IDENT_LEN} 位字母、数字、下划线或连字符",
                )
            elif tid in seen_tids:
                _err(problems, label, f"{where}.id", f"迁移标识 {tid!r} 重复")
            else:
                seen_tids.add(tid)
            if state_set and source not in state_set:
                _err(problems, label, f"{where}.source", f"迁移 {tid!r} 的源状态 {source!r} 未在 states 中声明")
            if state_set and target not in state_set:
                _err(problems, label, f"{where}.target", f"迁移 {tid!r} 的目标状态 {target!r} 未在 states 中声明")
            if action != TAU and (action_set and action not in action_set):
                _err(
                    problems,
                    label,
                    f"{where}.action",
                    f"迁移 {tid!r} 的动作 {action!r} 既不是 tau 也未在 actions 中声明",
                )
            if source in state_set and target in state_set and (action in action_set or action == TAU):
                transitions.append(Transition(tid, source, action, target))

    if problems:
        return None, sorted(problems, key=lambda p: (p.where, p.message))

    lts = LTS(
        label=label,
        states=tuple(states),
        initial=initial,
        actions=tuple(actions),
        transitions=tuple(transitions),
    )
    return lts, []
