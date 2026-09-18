# Teams @人员通知设计

## 1. 目标

为 Microsoft Teams 通知增加可选的 @人员能力。运维人员可以在 `teams.mentions` 中配置显示名和 Teams 用户标识；未配置人员时，现有通知 payload 保持不变；配置人员后，Adaptive Card 正文显示对应的 `<at>姓名</at>`，并在 `msteams.entities` 中提供可匹配的 mention 实体。

## 2. 配置契约

配置示例：

```yaml
teams:
  webhook_url: "https://你的工作流地址"
  mentions:
    - name: "张三"
      id: "zhangsan@example.com"
    - name: "李四"
      id: "lisi@example.com"
```

`TeamsConfig.mentions` 使用 Pydantic 模型表示，默认值为空序列。每项包含非空的 `name` 和 `id` 字符串；缺少字段或空字符串由配置校验拒绝。该配置不通过环境变量覆盖，继续只支持现有的 webhook URL 环境变量覆盖。

## 3. 模块与数据流

改动限定在以下位置：

- `src/public_ip_notifier/config.py`：新增有类型的 mention 配置模型，并将其挂载到 `TeamsConfig`。
- `src/public_ip_notifier/cli.py`：从已验证配置读取 mentions，并传给 `TeamsNotifier`。
- `src/public_ip_notifier/infra/teams.py`：保存 mention 配置，在构建卡片时生成正文 `TextBlock` 与 `msteams.entities`。
- `tests/unit/test_config.py`：验证 YAML mentions 能正确解析，以及非法 mention 被拒绝。
- `tests/unit/test_teams.py`：验证无 mentions 的兼容行为和多 mentions 的 payload。

notifier 接收不可变的 mention 配置序列，不依赖配置对象或 CLI。每个 mention 生成一组一一对应的字段：

```json
{
  "type": "mention",
  "text": "<at>张三</at>",
  "mentioned": {
    "id": "zhangsan@example.com",
    "name": "张三"
  }
}
```

## 4. 卡片行为

- `mentions` 为空时：不增加正文 `TextBlock`，`msteams` 保持 `{"entities": []}`。
- `mentions` 非空时：在现有标题 `TextBlock` 前插入一个 `TextBlock`，其 `text` 是按配置顺序以单个空格连接的 `<at>姓名</at>`；同时按相同顺序生成 `msteams.entities`。
- 生成逻辑只使用配置中的已校验字符串，不对姓名或标识做额外规范化，保证实体文本与正文严格匹配。
- webhook 请求、错误转换和现有 IP 变更内容不改变。

## 5. 测试与验收

测试必须离线运行，不引入新依赖。验收条件：

1. 有效 YAML 能解析出两个 mention，名称和标识顺序与配置一致。
2. 缺少 `name`/`id` 或值为空时，`load_config` 抛出 Pydantic `ValidationError`。
3. 未配置 mentions 的现有 Teams 测试继续通过，并断言 payload 没有 mention 文本且实体为空。
4. 配置多个 mentions 时，正文包含 `<at>张三</at> <at>李四</at>`，实体数量、顺序、`text`、`mentioned.id` 和 `mentioned.name` 均匹配。
5. `ruff check .`、`ruff format --check .`、相关模块 `mypy` 和完整 `pytest -q` 通过。

