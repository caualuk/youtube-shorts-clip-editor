"""Cliente LLM opcional (API da Anthropic) com controle de custos.

Por padrão a Skill NÃO chama API nenhuma: dentro do Claude, quem faz a análise semântica é o
próprio Claude lendo os arquivos de analise/. Este módulo existe para execução 100% automática
fora de uma conversa (ex.: lote noturno). Ative em config: "llm": {"enabled": true} e defina
ANTHROPIC_API_KEY. O gasto estimado é acumulado em state.json e a chamada é recusada se o
orçamento (llm.max_budget_usd) for ultrapassado.
"""
from __future__ import annotations

import json
import os
import re

from .core import PipelineError, ProjectState, log


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


def call_llm(prompt: str, cfg: dict, state: ProjectState, system: str = "") -> str:
    lc = cfg["llm"]
    if not lc.get("enabled"):
        raise PipelineError("LLM desativado (config llm.enabled=false).")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise PipelineError("ANTHROPIC_API_KEY não definido.")
    try:
        import anthropic
    except ImportError as e:
        raise PipelineError("pip install anthropic") from e
    usage = state.data.setdefault("llm_usage", {"usd": 0.0, "calls": 0})
    est_in = _estimate_tokens(system + prompt)
    est_cost = est_in * lc["usd_per_million_input_tokens"] / 1e6 + \
        lc["max_output_tokens"] * lc["usd_per_million_output_tokens"] / 1e6
    if usage["usd"] + est_cost > lc["max_budget_usd"]:
        raise PipelineError(f"Orçamento de API excedido: gasto {usage['usd']:.3f} + estimado {est_cost:.3f} "
                            f"> limite {lc['max_budget_usd']} USD. Aumente llm.max_budget_usd se desejar.")
    client = anthropic.Anthropic()
    msg = client.messages.create(model=lc["model"], max_tokens=lc["max_output_tokens"], system=system,
                                 messages=[{"role": "user", "content": prompt}])
    cost = msg.usage.input_tokens * lc["usd_per_million_input_tokens"] / 1e6 + \
        msg.usage.output_tokens * lc["usd_per_million_output_tokens"] / 1e6
    usage["usd"] = round(usage["usd"] + cost, 5)
    usage["calls"] += 1
    state.save()
    log.info("LLM: %d in / %d out tokens, custo ~US$ %.4f (acumulado %.4f)", msg.usage.input_tokens,
             msg.usage.output_tokens, cost, usage["usd"])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)
