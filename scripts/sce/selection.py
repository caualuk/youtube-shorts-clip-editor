"""Etapa 4 — seleção dos 10 cortes.

Três fontes possíveis, por ordem de preferência:
1. analise/selection.json escrito pelo Claude (seleção editorial — recomendado);
2. --llm: pede a seleção a um modelo via API (custos controlados);
3. --auto: guloso sobre o score heurístico com penalização de redundância.

Em todos os casos a seleção passa por validate_selection(), que garante: 20–25 s após jump cuts,
início/fim em fronteira de frase, sem sobreposição, sem repetição de trecho.
Se não houver momentos suficientes acima do limiar, retorna menos cortes e registra a limitação
— nunca completa a cota com cortes ruins.
"""
from __future__ import annotations

from . import lexicon as lx
from .analyze import effective_duration
from .core import PipelineError, fmt_ts, log


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def auto_select(cands: list[dict], cfg: dict) -> tuple[list[dict], list[str]]:
    """Agendamento ponderado de intervalos (programação dinâmica): maximiza primeiro a QUANTIDADE de
    cortes bons sem sobreposição e, depois, a soma dos scores — melhor que o guloso, que tende a
    pegar janelas sobrepostas e desperdiçar o vídeo. Depois aplica filtro de redundância temática."""
    cc = cfg["clips"]
    gap = cc["min_gap_between_clips"]
    pool = sorted([c for c in cands if c["auto_score"] >= cc["min_auto_score"]], key=lambda c: c["end"])
    n, K = len(pool), cc["count"]
    import bisect
    ends = [c["end"] for c in pool]
    prev = [bisect.bisect_right(ends, c["start"] - gap) - 1 for c in pool]
    NEG = (-1, -1e9)
    dp = [[(0, 0.0)] + [NEG] * K for _ in range(n + 1)]
    take = [[False] * (K + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        c = pool[i - 1]
        for k in range(K + 1):
            dp[i][k] = dp[i - 1][k]
            if k > 0:
                base = dp[prev[i - 1] + 1][k - 1]
                if base != NEG:
                    cand = (base[0] + 1, base[1] + c["auto_score"])
                    if cand > dp[i][k]:
                        dp[i][k], take[i][k] = cand, True
    best_k = max(range(K + 1), key=lambda k: dp[n][k])
    chosen, i, k = [], n, best_k
    while i > 0 and k > 0:
        if take[i][k]:
            chosen.append(pool[i - 1])
            i, k = prev[i - 1] + 1, k - 1
        else:
            i -= 1
    final, notes = [], []
    for c in sorted(chosen, key=lambda x: -x["auto_score"]):
        cw = set(lx.content_words(c["text"]))
        if any(_jaccard(cw, set(lx.content_words(o["text"]))) > 0.45 for o in final):
            notes.append(f"Candidato em {fmt_ts(c['start'])} descartado por repetir o assunto de outro corte.")
            continue
        final.append(dict(c, justification=_auto_justification(c), source="auto"))
    if len(final) < K:
        notes.append(f"Apenas {len(final)} momentos atingiram o limiar de qualidade "
                     f"(score ≥ {cc['min_auto_score']}) sem sobreposição/repetição. "
                     "A Skill não completou a cota com cortes fracos.")
    final.sort(key=lambda c: c["start"])
    return final, notes


def _auto_justification(c: dict) -> str:
    s = c["scores"]
    top = sorted(s.items(), key=lambda x: -x[1])[:3]
    return ("Seleção automática (heurística). Pontos fortes: " +
            ", ".join(f"{k} {v:.0f}" for k, v in top) + f". Palavras-chave: {', '.join(c['keywords'][:4])}.")


def _snap(sents: list[dict], start: float, end: float) -> tuple[int, int]:
    s0 = min(range(len(sents)), key=lambda i: abs(sents[i]["start"] - start))
    s1 = min(range(len(sents)), key=lambda i: abs(sents[i]["end"] - end))
    return s0, max(s0, s1)


def validate_selection(items: list[dict], sents_full: list[dict], cfg: dict) -> tuple[list[dict], list[str]]:
    """Normaliza e valida uma seleção vinda do Claude/LLM. Corrige o que dá; rejeita o resto com motivo."""
    cc, jc = cfg["clips"], cfg["jump_cuts"]
    mp, kp = (jc["min_pause_seconds"], jc["keep_pause_seconds"]) if jc["enabled"] else (1e9, 0)
    ok, notes = [], []
    for k, it in enumerate(items):
        if "s0" in it and "s1" in it:
            s0, s1 = int(it["s0"]), int(it["s1"])
        elif "start" in it and "end" in it:
            s0, s1 = _snap(sents_full, float(it["start"]), float(it["end"]))
        else:
            notes.append(f"Item {k}: sem s0/s1 nem start/end — ignorado.")
            continue
        if not (0 <= s0 <= s1 < len(sents_full)):
            notes.append(f"Item {k}: índices de sentença fora do intervalo — ignorado.")
            continue
        words = [w for i in range(s0, s1 + 1) for w in sents_full[i]["words"]]
        eff = effective_duration(words, mp, kp)
        # ajuste fino: tenta encaixar em 20–25 s adicionando/removendo sentenças nas bordas
        tries = 0
        while eff < cc["min_duration"] and s1 + 1 < len(sents_full) and tries < 4:
            s1 += 1
            words = [w for i in range(s0, s1 + 1) for w in sents_full[i]["words"]]
            eff = effective_duration(words, mp, kp)
            tries += 1
        while eff > cc["max_duration"] and s1 > s0 and tries < 8:
            s1 -= 1
            words = [w for i in range(s0, s1 + 1) for w in sents_full[i]["words"]]
            eff = effective_duration(words, mp, kp)
            tries += 1
        if not (cc["min_duration"] - 0.6 <= eff <= cc["max_duration"]):
            notes.append(f"Item {k} ({fmt_ts(sents_full[s0]['start'])}): duração efetiva {eff:.1f}s fora de "
                         f"{cc['min_duration']}–{cc['max_duration']}s mesmo após ajuste — rejeitado.")
            continue
        c = {"s0": s0, "s1": s1, "start": words[0]["start"], "end": words[-1]["end"],
             "effective_duration": round(eff, 2), "words": words,
             "text": " ".join(w["w"] for w in words),
             "justification": it.get("justification", ""), "title": it.get("title"),
             "topic_label": it.get("topic_label"), "scores": it.get("scores", {}),
             "auto_score": it.get("score"), "source": it.get("source", "claude"),
             "topic": it.get("topic", 0), "keywords": it.get("keywords", [])}
        if not sents_full[s1]["terminal"]:
            notes.append(f"Item {k}: termina sem pontuação final — verifique se a conclusão está completa.")
        overlap = [o for o in ok if not (c["end"] <= o["start"] or c["start"] >= o["end"])]
        if overlap:
            notes.append(f"Item {k}: sobrepõe outro corte selecionado — rejeitado (sem trechos repetidos).")
            continue
        ok.append(c)
    if len(ok) > cfg["clips"]["count"]:
        ok = ok[: cfg["clips"]["count"]]
    if not ok:
        raise PipelineError("Nenhum corte válido na seleção. Veja os motivos: " + " | ".join(notes))
    ok.sort(key=lambda c: c["start"])
    if len(ok) < cfg["clips"]["count"]:
        notes.append(f"Seleção final com {len(ok)} cortes (meta: {cfg['clips']['count']}).")
    return ok, notes


def llm_select(cands_md: str, cfg: dict, state) -> list[dict]:
    from .llm import call_llm, extract_json
    system = ("Você é um editor sênior de YouTube Shorts. Selecione momentos com gancho forte, "
              "autossuficientes, sem sobreposição. Responda só JSON.")
    prompt = (f"Escolha até {cfg['clips']['count']} candidatos. Retorne "
              '{"clips":[{"candidate":"C000","s0":int,"s1":int,"title":"...","justification":"..."}]}. '
              "Use s0/s1 exatamente como listados.\n\n" + cands_md)
    data = extract_json(call_llm(prompt, cfg, state, system))
    return [dict(x, source="llm") for x in data["clips"]]
