# 故障规程静默跳转复核（弱互模拟审计）

卫星地面站两份含静默内部跳转（`tau`）的故障规程复核应用：工程师在页面录入
各自有限状态、初始状态与迁移并提交审计，系统判定两个初始状态是否维持相同
的**可观察承诺**（弱互模拟，weak bisimulation）。

- 仅以 `tau` 环重命名而等价的规程 → 判定**等价**，关系中同时给出两个初始状态对；
- 一侧的可观察动作不能由另一侧以「静默前缀 + 同动作 + 静默后缀」承接 → **不等价**；
- 审查员可展开**首个淘汰状态对、挑战动作、按轮次递减的依据**：每条失败义务只
  引用更早轮次的淘汰结果（或无候选），状态对 / 动作 / 候选响应全部稳定排序，
  可自底向上逐轮复算。

仅依赖 Python 3.11 标准库，无第三方运行时依赖。

## 规程 JSON 格式

```json
{
  "states": ["S0", "S1"],
  "initial": "S0",
  "actions": ["alarm", "reset"],
  "transitions": [
    {"id": "t1", "source": "S0", "action": "tau",   "target": "S1"},
    {"id": "t2", "source": "S1", "action": "alarm", "target": "S0"}
  ]
}
```

约束（违规时一次列出全部问题并清除旧结论）：

- 每份规程 1–18 个状态，状态标识唯一（字母、数字、`_`、`-`）；
- 至多 4 种可观察动作（1–8 个非空白可打印 ASCII 字符），动作唯一，`tau` 保留；
- 迁移标识唯一；`source`/`target` 必须是已声明状态；`action` 为 `tau` 或已声明动作。

## 算法

弱迁移：`⇒τ` 为零步或多步 tau；可观察动作 `a` 的响应为 `⇒τ --a--> ⇒τ`。

1. R0 = S_A × S_B（全部状态对）；
2. 第 r 轮以**开轮关系 R_{r-1}** 同步检验每个存活对的双向模拟义务，失败者本轮
   一并淘汰（同一轮内不互相引用）；
3. 一整轮无淘汰即到达不动点 R∞。

因此第 r 轮被淘汰对的每条失败义务，其候选对必然已在第 1..r-1 轮淘汰，证据链
严格按轮次递减。判定等价当且仅当两个初始状态对（A 初始,B 初始）与其反向呈现
均存活于 R∞。

## HTTP 接口

| 路径 | 方法 | 说明 |
| --- | --- | --- |
| `/` | GET | 录入与审计页面 |
| `/healthz` | GET | 健康检查，返回 `{"status":"ok"}` |
| `/api/audit` | POST | 审计接口，请求体 `{"a": "<规程JSON文本>", "b": "<规程JSON文本>"}` |

审计成功返回 `{"valid": true, "report": {...}}`；输入无效返回 HTTP 400
`{"valid": false, "problems": [...]}`，一次包含两侧全部问题。

## Docker / Compose

构建并启动页面（宿主机端口可用 `HOST_PORT` 配置，默认 8080）：

```bash
docker compose up --build
# 自定义宿主机端口
HOST_PORT=9090 docker compose up --build
```

运行 `verify` 服务（构建检查 `compileall`、代码测试 `unittest`、接口/HTTP 冒烟；
完成即退出，并以退出码报告结果）：

```bash
docker compose run --build verify
# 或随编排一并运行，verify 结束后整体退出
docker compose up --build --abort-on-container-exit --exit-code-from verify
```

## 本机直接运行（无 Docker 时）

```bash
python3 -m app.server              # 起服务，默认 8080
python3 -m unittest discover -s tests
sh scripts/verify.sh               # 需先起服务；可用 BASE_URL 覆盖目标
```
