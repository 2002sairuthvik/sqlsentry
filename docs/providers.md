# LLM providers

sqlsentry depends only on the small `LLMProvider` interface, so any model can be used.
Check each service's current model list; the names below are examples.

## Groq (free tier, cloud)

```yaml
groq:
  type: openai_compatible
  base_url: https://api.groq.com/openai/v1
  model: openai/gpt-oss-120b
  api_key_env: GROQ_API_KEY
```

## Ollama (free, local, private)

```bash
ollama pull qwen2.5-coder:7b
```

```yaml
local:
  type: openai_compatible
  base_url: http://localhost:11434/v1
  model: qwen2.5-coder:7b
```

Small local models make more mistakes. The repair loop helps, and `sqlsentry eval` tells you
whether a model is good enough for your schema.

## vLLM / LM Studio / any OpenAI-compatible server

```yaml
vllm:
  type: openai_compatible
  base_url: http://my-gpu-box:8000/v1
  model: Qwen/Qwen2.5-Coder-32B-Instruct
```

## OpenAI, OpenRouter, Together, Fireworks

The same `openai_compatible` type with the service's `base_url`, `model` and `api_key_env`.
Request fields a service needs can go in `options` (they are merged into the request body).

## Claude

```bash
pip install "sqlsentry[anthropic]"
```

```yaml
claude:
  type: anthropic
  model: claude-opus-5-5
  api_key_env: ANTHROPIC_API_KEY
  options:
    effort: medium              # low | medium | high | xhigh | max
    fallbacks: true             # server-side refusal fallback; set false behind gateways/cloud platforms
```

This uses the official SDK with structured outputs, so responses are always schema-valid JSON.

## Your own provider

```python
from sqlsentry.llm.base import LLMProvider, LLMResult


class CompanyGateway(LLMProvider):
    def __init__(self, name, cfg):
        self.name, self.model = name, cfg.model

    def complete(self, system, messages, json_schema):
        text = call_my_gateway(system, messages)  # return raw text containing the JSON answer
        return LLMResult(text=text, model=self.model)
```

```yaml
gateway:
  type: my_company.llm:CompanyGateway
  model: internal-sql-model
```

## Restricting providers per datasource

```yaml
policy:
  allowed_providers: ["local", "vllm"]   # this schema never goes to a hosted API
```
