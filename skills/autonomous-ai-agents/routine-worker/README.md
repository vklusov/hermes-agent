# Routine Worker config examples

This file shows the `config.yaml` pieces needed to restore routine-worker routing on another Hermes installation.

The code path is intentionally provider-agnostic:

- define one or more providers in `custom_providers` when you use a non-built-in endpoint;
- reference them from `routine_worker` as `custom:<name>`;
- set the correct `api_mode` for each endpoint/model family.

Do **not** put real API keys in this file or in git. Use environment variables in `api_key_env`, or put secrets in your local `.env` / credential store.

---

## Minimal generic shape

```yaml
custom_providers:
  my-anthropic-compatible:
    base_url: https://example.com/v1
    api_key_env: MY_PROVIDER_API_KEY
    api_mode: anthropic_messages

  my-chat-compatible:
    base_url: https://example.com/v1
    api_key_env: MY_PROVIDER_API_KEY
    api_mode: chat_completions

  my-responses-compatible:
    base_url: https://example.com/v1
    api_key_env: MY_PROVIDER_API_KEY
    api_mode: codex_responses

routine_worker:
  default:
    provider: custom:my-anthropic-compatible
    model: minimax-m3

  marketplace_research:
    provider: custom:my-anthropic-compatible
    model: minimax-m3

  web_research:
    provider: custom:my-anthropic-compatible
    model: minimax-m3

  web_research_strong:
    provider: custom:my-anthropic-compatible
    model: qwen3.7-plus

  cheap_flash:
    provider: custom:my-chat-compatible
    model: deepseek-v4-flash

  kb_triage:
    provider: custom:my-responses-compatible
    model: gpt-5.4-mini
```

### Allowed `routine_worker` route fields

Each route may contain:

```yaml
provider: custom:provider-name   # or a built-in Hermes provider id
model: model-name
base_url: https://override.example/v1   # optional, usually prefer custom_providers
api_key: sk-...                         # optional, avoid committing this
api_mode: anthropic_messages            # optional, usually belongs on provider
```

`provider` and `model` are the normal fields. `base_url`, `api_key`, and `api_mode` are escape hatches for direct per-route overrides.

---

## Neurogate-style example

This is the layout used when Neurogate exposes several wire-compatible endpoints.

```yaml
custom_providers:
  neurogate-anthropic:
    base_url: https://YOUR-NEUROGATE-ENDPOINT/v1
    api_key_env: NEUROGATE_API_KEY
    api_mode: anthropic_messages

  neurogate-chat:
    base_url: https://YOUR-NEUROGATE-ENDPOINT/v1
    api_key_env: NEUROGATE_API_KEY
    api_mode: chat_completions

  neurogate-responses:
    base_url: https://YOUR-NEUROGATE-ENDPOINT/v1
    api_key_env: NEUROGATE_API_KEY
    api_mode: codex_responses

routine_worker:
  default:
    provider: custom:neurogate-anthropic
    model: minimax-m3

  marketplace_research:
    provider: custom:neurogate-anthropic
    model: minimax-m3

  web_research:
    provider: custom:neurogate-anthropic
    model: minimax-m3

  web_research_strong:
    provider: custom:neurogate-anthropic
    model: qwen3.7-plus

  cheap_flash:
    provider: custom:neurogate-chat
    model: deepseek-v4-flash

  kb_triage:
    provider: custom:neurogate-responses
    model: gpt-5.4-mini
```

If your existing main provider is already named `custom:neurogate`, you can use that name instead of `custom:neurogate-responses` for `kb_triage`, as long as its `api_mode` matches the model endpoint.

---

## Built-in provider example

If you do not use custom providers, route directly to built-in providers.

```yaml
routine_worker:
  default:
    provider: minimax
    model: minimax-m3

  cheap_flash:
    provider: deepseek
    model: deepseek-v4-flash

  kb_triage:
    provider: openrouter
    model: openai/gpt-5.4-mini
```

Exact provider and model IDs depend on your Hermes provider setup and available credentials.

---

## Installation / restore checklist

1. Apply or checkout the branch that contains the native `routine_worker` tool.
2. Add provider definitions under `custom_providers` if needed.
3. Add the `routine_worker` section.
4. Put API keys in environment variables, for example:

   ```bash
   export NEUROGATE_API_KEY=...
   ```

   or store them in your Hermes `.env` file.

5. Restart Hermes / gateway so the new config and tool schema are loaded:

   ```bash
   hermes gateway restart
   ```

6. Verify routing with a small smoke task and logs. The child worker should show the configured provider/model, not the global delegation default.

---

## Common pitfalls

- Use `custom:<name>` when referencing a custom provider. Do not write bare `custom`.
- Match `api_mode` to the endpoint. Common values are `anthropic_messages`, `chat_completions`, and `codex_responses`.
- Do not commit real `api_key` values.
- If workers still use the old delegation model, confirm the branch includes the dispatch forwarding fix and restart the running gateway/CLI session.
- Dynamic marketplace pages can still require browser/visual fallback even when model routing is correct.
