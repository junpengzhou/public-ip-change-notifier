# 公网 IP 变更通知器设计

## 1. 目标

构建一个 Python 3.11+ 常驻命令行程序，周期性地从多个 WAN 出口探测公网 IP。当已建立基线的 WAN 出口 IP 发生变化时，通过可配置的 Microsoft Teams Incoming Webhook 发送一条汇总通知。首次采集只建立本地状态，不发送通知。

程序从零搭建，使用 `uv` 管理项目和依赖，使用 `pytest` 测试，使用 `ruff` 格式化/检查，关键模块使用严格程度较高的 `mypy` 检查。

## 2. 方案选择

采用异步常驻 CLI：

- 使用 `asyncio` 管理生命周期和可配置的轮询间隔。
- 使用 `httpx.AsyncClient` 执行异步 HTTP 探测和 Teams webhook 请求。
- 每个 WAN 的 URL 按配置顺序 fallback；WAN 之间并发处理。
- 使用 YAML 作为面向运维人员的配置格式，使用 PyYAML 解析，再由 Pydantic 模型校验。
- 使用 JSON 文件持久化 WAN 到 IP 的状态，并通过临时文件加 `os.replace` 原子提交。

同步脚本不符合异步 IO 约束；交给外部 cron/Task Scheduler 的一次性命令无法统一处理状态事务、通知失败重试和优雅退出，因此不采用这两种方案。

## 3. 配置

推荐配置文件：

```yaml
interval_seconds: 60
state_file: "./data/public-ip-state.json"

teams:
  webhook_url: "https://outlook.office.com/webhook/..."

servers:
  wan1:
    - "https://ifconfig.me/ip"
    - "https://ipinfo.io/ip"
  wan2:
    - "https://api.ipify.org"
```

配置字段规则：

- `interval_seconds` 为正整数，控制每轮采集之间的间隔。
- `state_file` 为本地持久化 JSON 文件路径。
- `teams.webhook_url` 支持空值以禁用通知；配置变更通知但没有 URL 时记录结构化告警。
- `servers` 是 WAN 名称到 URL 列表的映射；每个 WAN 至少有一个 URL。
- URL 必须使用 `http` 或 `https` 协议。
- Webhook、间隔和状态文件等运行时值支持通过 `pydantic-settings` 环境变量覆盖，例如：
  - `PUBLIC_IP_NOTIFIER_TEAMS_WEBHOOK_URL`
  - `PUBLIC_IP_NOTIFIER_INTERVAL_SECONDS`
  - `PUBLIC_IP_NOTIFIER_STATE_FILE`

配置加载失败、字段校验失败或 URL 配置非法时，程序记录明确错误并以非零状态退出。

## 4. 模块边界

```text
src/public_ip_notifier/
  __main__.py
  cli.py
  config.py
  domain/
    models.py
  services/
    monitor.py
  infra/
    probes.py
    state_store.py
    teams.py
```

- `config.py`：读取 YAML，应用环境变量覆盖，生成经过 Pydantic 校验的配置对象。
- `domain/models.py`：定义探测结果、状态和变更事件等纯领域模型，不依赖基础设施。
- `infra/probes.py`：封装 HTTP 探测、超时、状态码和 IP 解析；同一 WAN 按 URL 顺序取第一个成功结果。
- `infra/state_store.py`：读取/写入 JSON 状态；写入使用临时文件和 `os.replace`。
- `infra/teams.py`：将领域通知转换为 Teams MessageCard 并发送 webhook。
- `services/monitor.py`：并发采集 WAN，比较状态，构建汇总通知，并协调状态提交。
- `cli.py`：解析 `--config`，创建依赖，运行轮询循环，处理优雅退出。

依赖方向保持 `api/入口 -> services -> domain <- infra` 的单向结构；领域层不导入基础设施或 CLI。

## 5. 采集与状态流程

每一轮执行以下步骤：

1. 为所有 WAN 启动异步采集任务。
2. 对每个 WAN 按 URL 配置顺序请求。请求超时、连接错误、非 2xx 响应或响应无法解析为合法 IP 时，记录该 URL 的失败并尝试下一个 URL。
3. 某 WAN 全部 URL 失败时，保留历史 IP；没有历史 IP 则保持未知。该失败不阻塞其他 WAN。
4. 读取现有状态，识别已经有历史值且本轮 IP 不同的 WAN。
5. 状态不存在、为空或仅出现新 WAN 时，只建立基线，不发送通知。
6. 如果存在已知 WAN 的 IP 变化，构建一条 MessageCard：包含时间、发生变化的 WAN 及旧/新 IP，并列出所有 WAN 当前已知 IP。
7. webhook 成功后原子提交本轮状态。webhook 失败时保留旧状态并记录错误，使下一轮可以重试同一变更。

状态文件初始格式为：

```json
{
  "wan1": "203.0.113.10",
  "wan2": "198.51.100.20"
}
```

只保存成功获取到的 IP；探测失败不会把历史 IP 覆盖为空值。

## 6. Teams 通知

使用 Incoming Webhook 兼容的 MessageCard JSON。通知内容至少包括：

- 采集时间。
- 触发通知的 WAN 及旧 IP/新 IP。
- 所有 WAN 的当前已知 IP，未知值明确标记为 `unknown`。

单轮多个 WAN 变化只发送一条汇总通知。Teams 返回非 2xx 或请求异常按通知失败处理，不吞异常，且不提交本轮变化状态。

## 7. 生命周期与错误处理

- HTTP 客户端在程序生命周期内复用，并在退出时关闭。
- 轮询间隔使用 `asyncio.Event` 等待，不使用阻塞式 `time.sleep`。
- 捕获 `SIGINT`/`SIGTERM` 后停止启动新轮次，等待当前任务收尾并退出。
- 使用 `structlog` 输出结构化日志，不保留 `print()` 调试语句。
- 状态 JSON 损坏、目录不可写或原子替换失败时报告明确错误；不使用裸 `except Exception` 吞异常。

## 8. 测试策略

使用 `pytest` 和离线 HTTP mock，禁止真实公网请求。测试覆盖：

- YAML 配置、环境变量覆盖、非法间隔和空 URL 校验。
- URL fallback：第一个 URL 失败或返回非法 IP 时使用后续 URL。
- 多 WAN 并发采集，单个 WAN 失败不影响其他 WAN。
- 首次运行只建立状态，不调用 Teams。
- 已知 WAN 变化时发送包含所有 WAN IP 的 MessageCard，并在 webhook 成功后提交状态。
- webhook 失败时状态不前进，下一轮仍能重试变更。
- 状态文件原子写入，以及损坏 JSON 的明确错误。
- CLI 单轮运行、周期循环和信号退出。

新增函数必须带类型注解；新增公共 API 使用 Google 风格 docstring。实现不引入未登记的第三方依赖，新增依赖原因在项目配置/变更说明中记录。

