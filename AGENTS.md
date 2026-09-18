# AGENTS.md - Python Project Guide

> 本文件用于约束所有 AI 编码助手（Copilot / Cursor / Claude Code / 各类 Agent）在本仓库中的行为。
> **优先级：本文件 > 任何 AI 的默认偏好。冲突时以本文件为准。**

---

## 1. 项目定位

- 语言：Python 3.11+
- 构建/依赖：`uv`（不要用 pip freeze 手写 requirements）
- 测试：`pytest`
- 格式化：`ruff format` + `ruff check`
- 类型：`mypy (strict-ish)`，关键模块必须过类型检查
- 包结构：分层 + 领域驱动轻量化（见 `docs/architecture.md`）

---

## 2. 硬性红线（违反即拒绝合并）

AI **禁止**做以下事情：

1. ❌ 引入新第三方依赖，除非：
   - 已在 `pyproject.toml` 的 `[project.optional-dependencies]` 中登记
   - 并且在 PR 描述里说明「为什么标准库做不到」
2. ❌ 修改 `pyproject.toml` / `uv.lock` 而不同步说明升级原因
3. ❌ 写 `print()` 调试语句留在代码里（用 `logging`，且走 `structlog`）
4. ❌ 用 `except Exception:` 吞异常（必须明确异常类型，或至少 `logger.exception`）
5. ❌ 新增函数不写类型注解
6. ❌ 新增公共 API 不写 docstring（Google 风格）
7. ❌ 在业务代码里写 `time.sleep`、硬编码路径、硬编码密钥/token
8. ❌ 直接改 `main` 分支 / 不跑测试就说「完成」

---

## 3. 必须遵守的编码规范

### 3.1 风格

- 行宽 **88**（跟 ruff 默认一致）
- import 顺序：`future → stdlib → third-party → local`，分组空行隔开
- 不用 `Any` —— 实在绕不开必须加注释 `# type: ignore[arg-type] # TODO: 收敛类型`
- 字符串：内部代码用双引号；f-string 优先

### 3.2 错误处理

```python
# ✅ 正确
try:
    payload = await client.fetch(user_id)
except httpx.HTTPStatusError as e:
    logger.warning("fetch_failed", user_id=user_id, status=e.response.status_code)
    raise UserFetchError(...) from e

# ❌ 禁止
except Exception:
    pass
```

### 3.3 异步

- IO 密集一律 `async/await`，禁止在 async 函数里跑阻塞调用（`requests`、`pandas.read_csv` 大文件）
- CPU 密集走 `anyio.to_thread.run_sync` 或单独 worker，不要阻塞 event loop

### 3.4 配置

- 所有配置走 `pydantic-settings` + 环境变量，**不允许在函数里 `os.environ.get` 散落**
- 敏感项必须从 `SecretStr` 取

### 3.5 测试

- 新功能必须带测试，放在 `tests/<模块名>/test_*.py`
- mock 用 `unittest.mock` 或 `pytest-mock`，**不要用 monkeypatch 改全局状态糊过去**
- 测试名语义化：`test_<行为>_when_<条件>_then_<预期>`
- 涉及外部服务的测试必须能靠 `respx` / `responses` 离线跑通，**不允许真联网**

---

## 4. AI 改代码的标准流程（必须按这个来）

收到任务时，AI 请按以下步骤输出，不要直接甩一坨 diff：

1. **先复述需求**：用 2–3 句确认你理解的是「用户要的行为」，不是「你猜的行为」
2. **先定位**：指出要改哪些文件 / 函数，为什么放这里
3. **小步改动**：一次 PR 只做一件事；不要顺手「顺手重构别的模块」
4. **自检清单**（输出时附上）：
   - [ ] `ruff check .` 通过
   - [ ] `ruff format --check .` 通过
   - [ ] `mypy <改动模块>` 通过
   - [ ] `pytest -q` 全绿
   - [ ] 没引入新依赖 / 没留 debug 代码
5. **不会的就说不会**：遇到不清楚的领域规则，明确问，不要编造 API 或假装有实现

> 如果一次改动超过 ~200 行，主动提议拆成多个提交，并说明拆分方式。

---

## 5. 目录约定（AI 新增文件请对号入座）

```
src/<project>/
  ├── api/        # 路由/入口，只做参数校验+调用service
  ├── services/   # 业务逻辑，纯函数或类，不碰框架对象
  ├── domain/     # 模型/值对象/异常定义
  ├── infra/      # 数据库、HTTP client、缓存等外部适配
  ├── config.py
tests/
  ├── unit/
  └── integration/
```

**依赖方向单向**：`api → services → domain ← infra`
`infra` 可以依赖 `domain`，但 `domain` 绝不能 import `infra` / `api`。

---

## 6. 提交信息约定

用 Conventional Commits：

```
feat(payments): 支持退款幂等键
fix(cli): --dry-run 不再真的写盘
refactor(domain): 拆出 Money 值对象
test(services): 补幂等用例
chore: 升级 pydantic 到 2.9
```

AI 写 commit message 时**必须**带 scope，别只写 `feat: update stuff`。

---

## 7. 明确不许 AI 碰的区

- `migrations/`（schema 变更必须由人审，AI 只能生成脚本并标注「需人工 review」）
- `secrets/`、`.env*`
- 任何 `*.proto` / 生成代码（生成了要注明 `@generated`）

---

## 8. 一句话底线

> **宁可少写 30 行正确代码，也不要写 100 行看起来能跑、但没人敢动的代码。**
> AI 输出若让我需要花更多时间去猜意图，那就是失败输出。

---