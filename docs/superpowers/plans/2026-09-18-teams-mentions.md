# Teams @人员通知 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional Teams mention configuration that inserts matching `<at>` tags and `msteams.entities` while preserving the current payload when no people are configured.

**Architecture:** Keep validation in `config.py`, pass validated immutable mention data from `cli.py` into `TeamsNotifier`, and keep all Adaptive Card formatting in `infra/teams.py`. The default mention collection is empty, so existing callers and payload behavior remain compatible.

**Tech Stack:** Python 3.11+, Pydantic 2, PyYAML, httpx, pytest, respx, ruff, mypy.

---

### Task 1: Add validated mention configuration

**Files:**
- Modify: `src/public_ip_notifier/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing YAML parsing test**

Add `test_load_config_when_teams_mentions_are_configured_then_preserves_order_and_values` that writes a YAML file with two `teams.mentions` entries, calls `load_config`, and asserts the resulting entries expose `name` and `id` in the configured order.

```python
def test_load_config_when_teams_mentions_are_configured_then_preserves_order_and_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "teams:\n"
        "  mentions:\n"
        "    - name: 张三\n"
        "      id: zhangsan@example.com\n"
        "    - name: 李四\n"
        "      id: lisi@example.com\n"
        "servers:\n"
        "  wan1:\n"
        "    - https://example.test/ip\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert [(item.name, item.id) for item in config.teams.mentions] == [
        ("张三", "zhangsan@example.com"),
        ("李四", "lisi@example.com"),
    ]
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `uv run pytest tests/unit/test_config.py::test_load_config_when_teams_mentions_are_configured_then_preserves_order_and_values -q`

Expected: FAIL because `TeamsConfig` has no `mentions` field.

- [ ] **Step 3: Add the minimal Pydantic models**

In `config.py`, add a public `TeamsMention` model with `name: str` and `id: str`, and add `mentions: tuple[TeamsMention, ...] = ()` to `TeamsConfig`. Add a field validator that rejects blank or whitespace-only `name` and `id` values. Keep `webhook_url` unchanged.

```python
class TeamsMention(BaseModel):
    """A Teams user that can be mentioned in a notification card."""

    name: str
    id: str

    @field_validator("name", "id")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Teams mention values must not be empty")
        return value


class TeamsConfig(BaseModel):
    """Teams notification configuration."""

    webhook_url: SecretStr | None = None
    mentions: tuple[TeamsMention, ...] = ()
```

- [ ] **Step 4: Add failing validation tests**

Add both tests below to assert missing and blank values are rejected:

```python
def test_load_config_when_teams_mention_name_is_missing_then_rejects_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "teams:\n"
        "  mentions:\n"
        "    - id: zhangsan@example.com\n"
        "servers:\n"
        "  wan1:\n"
        "    - https://example.test/ip\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(path)


def test_load_config_when_teams_mention_id_is_blank_then_rejects_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "teams:\n"
        "  mentions:\n"
        "    - name: 张三\n"
        "      id: '   '\n"
        "servers:\n"
        "  wan1:\n"
        "    - https://example.test/ip\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(path)
```

- [ ] **Step 5: Run all config tests**

Run: `uv run pytest tests/unit/test_config.py -q`

Expected: PASS, including the new parsing and validation tests.

- [ ] **Step 6: Commit the configuration change**

```bash
git add src/public_ip_notifier/config.py tests/unit/test_config.py
git commit -m "feat(config): support Teams mention settings"
```

### Task 2: Pass mentions into the notifier

**Files:**
- Modify: `src/public_ip_notifier/cli.py:93-100`
- Modify: `src/public_ip_notifier/infra/teams.py:18-24`
- Test: `tests/unit/test_teams.py`

- [ ] **Step 1: Write the failing constructor-level behavior test**

Update the existing notifier test construction to pass `mentions=()` explicitly. Add the future multi-mention test in Task 3 using the same constructor shape. This establishes the intended public constructor parameter before changing production code.

- [ ] **Step 2: Run the focused Teams test and verify it fails**

Run: `uv run pytest tests/unit/test_teams.py::test_teams_notifier_when_change_then_posts_adaptive_card_attachment -q`

Expected: FAIL with a constructor argument error because `TeamsNotifier` does not yet accept mentions.

- [ ] **Step 3: Add a typed optional mentions parameter and wire the CLI**

Import `TeamsMention` in `cli.py`, pass `config.teams.mentions` to `TeamsNotifier`, and update `TeamsNotifier.__init__` to accept `Sequence[TeamsMention] = ()`. Store a tuple copy so later caller mutation cannot change payload generation.

```python
def __init__(
    self,
    client: httpx.AsyncClient,
    webhook_url: str,
    mentions: Sequence[TeamsMention] = (),
) -> None:
    self._client = client
    self._webhook_url = webhook_url
    self._mentions = tuple(mentions)
```

- [ ] **Step 4: Run the focused Teams test**

Run: `uv run pytest tests/unit/test_teams.py::test_teams_notifier_when_change_then_posts_adaptive_card_attachment -q`

Expected: PASS with the existing empty `msteams.entities` payload.

- [ ] **Step 5: Commit constructor and CLI wiring**

```bash
git add src/public_ip_notifier/cli.py src/public_ip_notifier/infra/teams.py tests/unit/test_teams.py
git commit -m "feat(teams): pass configured mentions to notifier"
```

### Task 3: Generate mention text and entities

**Files:**
- Modify: `src/public_ip_notifier/infra/teams.py`
- Test: `tests/unit/test_teams.py`

- [ ] **Step 1: Add the failing multi-mention payload test**

Add `test_teams_notifier_when_mentions_are_configured_then_adds_at_text_and_entities`. Register the webhook with `respx`, construct `TeamsNotifier` with two `TeamsMention` objects, send one change, decode the JSON request, and assert:

```python
assert payload["attachments"][0]["content"]["body"][0] == {
    "type": "TextBlock",
    "text": "<at>张三</at> <at>李四</at>",
    "wrap": True,
}
assert payload["attachments"][0]["content"]["msteams"]["entities"] == [
    {
        "type": "mention",
        "text": "<at>张三</at>",
        "mentioned": {"id": "zhangsan@example.com", "name": "张三"},
    },
    {
        "type": "mention",
        "text": "<at>李四</at>",
        "mentioned": {"id": "lisi@example.com", "name": "李四"},
    },
]
```

- [ ] **Step 2: Run the new test and verify it fails**

Run: `uv run pytest tests/unit/test_teams.py::test_teams_notifier_when_mentions_are_configured_then_adds_at_text_and_entities -q`

Expected: FAIL because the generated body has the existing title as its first item and `entities` is empty.

- [ ] **Step 3: Implement the minimal payload generation**

Change `_build_payload` from a static method to an instance method so it can read `self._mentions`. Build `mention_text` and `mention_entities` in configuration order. Insert the mention `TextBlock` at the beginning of `body` only when the collection is non-empty, and set `msteams.entities` to the generated list; otherwise retain an empty list.

```python
mention_entities = [
    {
        "type": "mention",
        "text": f"<at>{mention.name}</at>",
        "mentioned": {"id": mention.id, "name": mention.name},
    }
    for mention in self._mentions
]
mention_text = " ".join(entity["text"] for entity in mention_entities)
mention_block = (
    [{"type": "TextBlock", "text": mention_text, "wrap": True}]
    if mention_entities
    else []
)
body = mention_block + [
    {
        "type": "TextBlock",
        "text": "⚠️公网出口 IP 地址变更通知",
        "size": "Medium",
        "weight": "Bolder",
        "wrap": True,
    },
    {
        "type": "TextBlock",
        "text": f"探测时间: {timestamp}",
        "wrap": True,
    },
    {
        "type": "TextBlock",
        "text": "变更内容",
        "weight": "Bolder",
        "spacing": "Medium",
    },
    {"type": "FactSet", "facts": change_facts},
    {
        "type": "TextBlock",
        "text": "当前所有WAN口的公网出口IP",
        "weight": "Bolder",
        "spacing": "Medium",
    },
    {"type": "FactSet", "facts": current_facts},
]
```

Use a typed local structure compatible with the existing `dict[str, object]` payload style; do not add a third-party model or change webhook behavior.

- [ ] **Step 4: Run the focused Teams tests**

Run: `uv run pytest tests/unit/test_teams.py -q`

Expected: PASS for empty mentions, configured mentions, and webhook failure behavior.

- [ ] **Step 5: Commit payload generation**

```bash
git add src/public_ip_notifier/infra/teams.py tests/unit/test_teams.py
git commit -m "feat(teams): add adaptive card mentions"
```

### Task 4: Format, type-check, and run the complete suite

**Files:**
- Modify only files identified by tooling, if needed: `src/public_ip_notifier/config.py`, `src/public_ip_notifier/cli.py`, `src/public_ip_notifier/infra/teams.py`, `tests/unit/test_config.py`, `tests/unit/test_teams.py`

- [ ] **Step 1: Run Ruff checks**

Run: `uv run ruff format --check .` and `uv run ruff check .`

Expected: both commands exit 0. If formatting is needed, run `uv run ruff format` only on the touched files, review the diff, then rerun both checks.

- [ ] **Step 2: Run strict type checks on changed modules**

Run: `uv run mypy src/public_ip_notifier/config.py src/public_ip_notifier/cli.py src/public_ip_notifier/infra/teams.py`

Expected: success with no errors.

- [ ] **Step 3: Run the complete test suite**

Run: `uv run pytest -q`

Expected: all tests pass with no real network requests.

- [ ] **Step 4: Review the final diff and repository state**

Run: `git diff --check`, `git status --short`, and `git log --oneline -6`. Confirm no new dependency, secret, `.env` file, debug `print()`, or unrelated refactor is present.
