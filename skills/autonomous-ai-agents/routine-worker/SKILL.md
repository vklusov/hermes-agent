---
name: routine-worker
description: "Use when routing broad routine work to cheaper or source-specific worker agents through the native routine_worker tool."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [delegation, workers, routing, marketplace, research]
    related_skills: [hermes-agent, marketplace-research]
---

# Routine Worker

## Overview

`routine-worker` is the skill layer for Hermes' native `routine_worker` tool. It is intended for broad, routine work where the main agent should stay as the orchestrator while cheaper or source-specific workers collect facts in parallel.

The native tool wraps `delegate_task` with safe presets and optional per-task model routing from `config.yaml`. This keeps expensive main-model turns focused on orchestration, validation, and final synthesis instead of spending them on routine source collection.

## When to Use

Use this skill when the user asks for:

- marketplace/product research across multiple stores or sources;
- broad web research where independent source lanes can run in parallel;
- source-specific checks that should return compact summaries to the main agent;
- local knowledge-base triage where a worker can collect facts and the main agent decides what to update.

Do **not** use this skill for:

- tiny single-step checks where one direct tool call is enough;
- tasks that require user interaction inside the worker;
- purchases, logins, email sends, public posts, or other external writes;
- destructive infrastructure operations;
- long-running durable tasks that should be cron jobs instead.

## Native Tool

The backing tool is:

```text
routine_worker(task_type, objective, context?, sources?, toolsets?, background?)
```

Important behavior:

- The main agent remains the orchestrator.
- Workers are leaf agents by default.
- Presets choose source lanes and toolsets.
- Per-task model/provider routing is resolved from `routine_worker` config when present.
- If no route is configured, Hermes falls back to the normal delegation model.

## Presets

| Preset | Use case | Default shape |
|---|---|---|
| `marketplace_research` | Product search across marketplaces | Parallel source lanes for Yandex Market, Wildberries, and search/Ozon fallback |
| `web_research` | Routine source collection | Parallel web-source lanes |
| `web_research_strong` | Higher-stakes web research | Stronger configured worker route, same orchestration pattern |
| `cheap_flash` | Very cheap quick checks | Fast/cheap route when configured |
| `kb_triage` | Local KB/source-of-truth triage | Focused worker with file/session context |
| `single` | One generic routine worker | Single delegated worker |

## Config Example

Add routes under `routine_worker` in `config.yaml`:

```yaml
routine_worker:
  default:
    provider: custom:my-anthropic-compatible-endpoint
    model: minimax-m3

  marketplace_research:
    provider: custom:my-anthropic-compatible-endpoint
    model: minimax-m3

  web_research_strong:
    provider: custom:my-strong-worker-endpoint
    model: qwen3.7-plus

  cheap_flash:
    provider: custom:my-chat-endpoint
    model: deepseek-v4-flash

  kb_triage:
    provider: custom:my-main-compatible-endpoint
    model: gpt-5.4-mini
```

The provider names must already exist in Hermes provider configuration. Use `custom:<name>` for custom providers, not bare `custom`.

For a fuller restoreable provider/routing example, see `README.md` in this skill directory. It includes generic, Neurogate-style, and built-in provider layouts.

## Procedure

1. Restate the user's goal as concrete filters and constraints.
2. Pick the narrowest preset that fits the task.
3. Pass explicit safety boundaries in `context`:
   - no purchases;
   - no logins;
   - no external writes;
   - cite source URLs;
   - mark uncertain prices/availability as snapshots.
4. Let workers collect source-specific facts.
5. Validate important claims directly when needed.
6. Produce a compact final answer with a clear recommendation and uncertainty notes.

## Marketplace Example

```json
{
  "task_type": "marketplace_research",
  "objective": "Найти простой дешёвый брендовый Wi‑Fi 2.4 ГГц роутер для режима точки доступа по RJ45",
  "context": "Не покупать, не логиниться. Исключить no-name. Проверить RJ45, AP mode, цену и ссылку. Цены считать snapshot, перед покупкой перепроверить.",
  "sources": ["Yandex Market", "Wildberries", "Ozon/search"]
}
```

## Main-Agent Responsibilities

The main agent must still:

- choose the preset;
- detect weak or contradictory worker evidence;
- avoid claiming that dynamic marketplace pages were fully verified when only snippets were available;
- run direct verification for any critical fact;
- synthesize the final answer in the user's requested language/style.

## Common Pitfalls

1. **Expecting the skill alone to create a tool.** The `SKILL.md` only teaches the behavior. The native `routine_worker` implementation must be present in the Hermes codebase.
2. **Forgetting provider API modes.** Some models need `responses`, others `chat_completions` or `anthropic_messages`. Configure the custom provider correctly.
3. **Using routine workers for tiny tasks.** If one direct `web_search` or `read_file` call answers the question, use the direct tool.
4. **Trusting dynamic marketplace data too strongly.** Ozon, Wildberries, and Yandex Market often serve dynamic or anti-bot pages. Treat snippets as snapshots and label uncertain availability.
5. **Letting workers perform side effects.** Workers should collect and summarize. Purchases, sends, posts, writes, and infrastructure changes remain main-agent actions and require explicit user approval.

## Verification Checklist

- [ ] `routine_worker` appears in the relevant toolset.
- [ ] The selected preset maps to the expected provider/model route.
- [ ] A smoke task launches workers successfully.
- [ ] Worker summaries include source URLs or clear uncertainty labels.
- [ ] The final answer is synthesized by the main agent, not copied blindly from a worker.
