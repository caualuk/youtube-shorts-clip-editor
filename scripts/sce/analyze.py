"""Etapa 3 — análise de conteúdo: frases, energia de áudio, mudanças de cena, assuntos
e geração de candidatos de 20–25 s (já considerando a compactação por jump cuts).

Tudo é feito em streaming: o áudio é lido em blocos de 8 kHz pelo pipe do FFmpeg e a detecção
de cena roda sobre uma versão reduzida (160 px) — memória constante mesmo em vídeos de horas.

A pontuação heurística aqui é uma PRÉ-TRIAGEM. A seleção editorial final é feita pelo Claude
(lendo analise/candidates.md) ou, no modo --auto, pelos mesmos scores heurísticos.
"""
from __future__ import annotations

import math
import re
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np

from . import lexicon as lx
from .core import fmt_ts, log, run

TERMINAL = re.compile(r"[.!?…]['\"”)]*$")


# ------------------------------------------------------------------ frases
def build_sentences(tr: dict, max_gap: float = 0.9) -> list[dict]:
    words = [w for s in tr["segments"] for w in s["words"] if w["w"]]
    seg_lp = {}
    for s in tr["segments"]:
        for w in s["words"]:
            seg_lp[id(w)] = s.get("avg_logprob")
    sents, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 99
        long_enough = len(cur) >= 4
        if TERMINAL.search(w["w"]) or gap > max_gap and long_enough or gap > 2.0 or len(cur) > 60:
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    out = []
    for i, ws in enumerate(sents):
        lps = [seg_lp.get(id(w)) for w in ws if seg_lp.get(id(w)) is not None]
        text = " ".join(w["w"] for w in ws)
        out.append({"id": i, "start": ws[0]["start"], "end": ws[-1]["end"], "text": text, "words": ws,
                    "terminal": bool(TERMINAL.search(ws[-1]["w"])),
                    "avg_logprob": float(np.mean(lps)) if lps else None})
    return out


# ------------------------------------------------------------------ áudio
def audio_energy(video: Path, duration: float, win: float = 0.5, sr: int = 8000) -> np.ndarray:
    """RMS (dBFS) por janela, lido em streaming."""
    hop = int(sr * win)
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1",
                             "-ar", str(sr), "-f", "s16le", "-"], stdout=subprocess.PIPE)
    vals, buf = [], b""
    while True:
        chunk = proc.stdout.read(hop * 2 * 64)
        if not chunk:
            break
        buf += chunk
        n = len(buf) // (hop * 2)
        if n:
            arr = np.frombuffer(buf[: n * hop * 2], dtype=np.int16).astype(np.float32).reshape(n, hop) / 32768
            rms = np.sqrt(np.mean(arr ** 2, axis=1) + 1e-12)
            vals.extend((20 * np.log10(rms)).tolist())
            buf = buf[n * hop * 2:]
    proc.wait()
    return np.array(vals, dtype=np.float32)


def scene_changes(video: Path, threshold: float) -> list[float]:
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-an", "-vf",
             f"scale=160:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
            desc="detecção de cenas", check=False)
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", p.stderr)]


# ------------------------------------------------------------------ assuntos
def topic_segments(sents: list[dict], window: int = 6) -> list[int]:
    """TextTiling simplificado: queda de coesão lexical entre janelas vizinhas = mudança de assunto."""
    n = len(sents)
    bags = [Counter(lx.content_words(s["text"])) for s in sents]
    def cos(a: Counter, b: Counter) -> float:
        num = sum(a[k] * b[k] for k in a if k in b)
        den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
        return num / den if den else 0.0
    gaps = []
    for i in range(1, n):
        left = sum((bags[j] for j in range(max(0, i - window), i)), Counter())
        right = sum((bags[j] for j in range(i, min(n, i + window))), Counter())
        gaps.append(cos(left, right))
    topic, tid = [0] * n, 0
    if gaps:
        arr = np.array(gaps)
        thr = arr.mean() - arr.std() * 0.8
        last = 0
        for i in range(1, n):
            g = gaps[i - 1]
            local_min = (i - 2 < 0 or g <= gaps[i - 2]) and (i >= len(gaps) or g <= gaps[i])
            if g < thr and local_min and i - last >= 4:
                tid += 1
                last = i
            topic[i] = tid
    return topic


def keywords_for(text: str, df: Counter, n_docs: int, k: int = 6) -> list[str]:
    tf = Counter(lx.content_words(text))
    scored = {w: c * math.log((1 + n_docs) / (1 + df[w])) for w, c in tf.items()}
    return [w for w, _ in sorted(scored.items(), key=lambda x: -x[1])[:k]]


# ------------------------------------------------------------------ candidatos
def effective_duration(words: list[dict], min_pause: float, keep_pause: float) -> float:
    """Duração após remover pausas longas (jump cuts)."""
    if not words:
        return 0.0
    total = words[-1]["end"] - words[0]["start"]
    for a, b in zip(words, words[1:]):
        gap = b["start"] - a["end"]
        if gap > min_pause:
            total -= gap - keep_pause
    return total + 0.35  # respiro inicial/final


def _hook_score(first: str) -> float:
    t = lx.norm(first)
    toks = lx.tokens(first)
    s = 0.0
    if "?" in first:
        s += 25
    if re.search(r"\d", first):
        s += 15
    s += 12 * sum(1 for m in lx.HOOK_MARKERS if m in toks)
    if toks and toks[0] in ("voce", "you", "imagine", "pare", "nunca", "ninguem", "o", "a"):
        s += 8
    if 5 <= len(toks) <= 18:
        s += 15
    elif len(toks) > 28:
        s -= 15
    if toks and toks[0] in lx.DEPENDENT_STARTS:
        s -= 35
    return max(0.0, min(100.0, s + 20))


def score_candidate(c: dict, energy: np.ndarray, win: float, sents: list[dict], df: Counter, n_docs: int) -> dict:
    text = c["text"]
    toks = lx.tokens(text)
    first = sents[c["s0"]]["text"]
    words = c["words"]
    span = words[-1]["end"] - words[0]["start"]
    wps = len(words) / max(1e-3, span)
    # retenção: ritmo de fala + variação de energia
    i0, i1 = int(c["start"] / win), max(int(c["start"] / win) + 1, int(c["end"] / win))
    seg = energy[i0:i1] if len(energy) else np.array([-30.0])
    seg = seg[np.isfinite(seg)] if seg.size else np.array([-30.0])
    loud = float(np.mean(seg)) if seg.size else -40
    dyn = float(np.std(seg)) if seg.size else 0
    pace = 100 - min(100, abs(wps - 2.8) * 45)
    retention = 0.5 * pace + 0.5 * min(100, dyn * 9)
    hook = _hook_score(first)
    curiosity = min(100, 20 * text.count("?") + 10 * sum(1 for m in lx.HOOK_MARKERS if m in toks) + 10)
    clarity_parts = [sents[i]["terminal"] for i in range(c["s0"], c["s1"] + 1)]
    clarity = 100 * sum(clarity_parts) / len(clarity_parts)
    starts_dep = bool(toks) and toks[0] in lx.DEPENDENT_STARTS
    has_dangling_ref = bool(toks) and toks[0] in ("isso", "esse", "essa", "ele", "ela", "this", "that", "it")
    independence = 100 - 45 * starts_dep - 25 * has_dangling_ref - (0 if sents[c["s1"]]["terminal"] else 30)
    opinion = sum(1 for m in lx.OPINION_MARKERS if m in toks)
    comments = min(100, 25 * opinion + 15 * text.count("?") + 10)
    humor = sum(1 for m in lx.HUMOR_MARKERS if m in toks)
    kw = keywords_for(text, df, n_docs)
    distinct = np.mean([math.log((1 + n_docs) / (1 + df[w])) for w in kw]) if kw else 0
    relevance = min(100, distinct * 25)
    share = min(100, 0.4 * hook + 0.3 * comments + 20 * humor + 10 * bool(re.search(r"\d", text)))
    lps = [sents[i]["avg_logprob"] for i in range(c["s0"], c["s1"] + 1) if sents[i]["avg_logprob"] is not None]
    asr_conf = 100 if not lps else max(0, min(100, 100 + float(np.mean(lps)) * 80))
    av_quality = max(0, min(100, (loud + 45) * 3)) * 0.5 + asr_conf * 0.5
    scores = {"retencao": retention, "curiosidade": curiosity, "clareza": clarity, "compartilhamento": share,
              "relevancia": relevance, "forca_primeira_frase": hook, "comentarios": comments,
              "independencia": max(0, independence), "qualidade_av": av_quality}
    weights = {"retencao": 1.2, "curiosidade": 1.0, "clareza": 1.0, "compartilhamento": 0.8, "relevancia": 0.8,
               "forca_primeira_frase": 1.5, "comentarios": 0.6, "independencia": 1.5, "qualidade_av": 0.8}
    total = sum(scores[k] * w for k, w in weights.items()) / sum(weights.values())
    c["scores"] = {k: round(v, 1) for k, v in scores.items()}
    c["auto_score"] = round(total, 1)
    c["keywords"] = kw
    c["profanity"] = lx.profanity_hits(text)
    c["sensitive"] = lx.sensitive_hits(text)
    c["wps"] = round(wps, 2)
    c["mean_loudness_db"] = round(loud, 1)
    return c


def build_candidates(sents: list[dict], topics: list[int], cfg: dict) -> list[dict]:
    cc, jc = cfg["clips"], cfg["jump_cuts"]
    mn, mx = cc["min_duration"], cc["max_duration"]
    mp, kp = (jc["min_pause_seconds"], jc["keep_pause_seconds"]) if jc["enabled"] else (1e9, 0)
    cands = []
    for i in range(len(sents)):
        if i > 0 and not sents[i - 1]["terminal"] and sents[i]["start"] - sents[i - 1]["end"] < 0.5:
            continue  # começaria no meio de uma frase
        words = []
        for j in range(i, len(sents)):
            words = words + sents[j]["words"]
            eff = effective_duration(words, mp, kp)
            if eff > mx:
                break
            if eff >= mn and sents[j]["terminal"]:
                cands.append({"s0": i, "s1": j, "start": round(words[0]["start"], 3),
                              "end": round(words[-1]["end"], 3), "effective_duration": round(eff, 2),
                              "topic": topics[i], "words": list(words),
                              "text": " ".join(w["w"] for w in words)})
    # fallback: transcrição sem pontuação → aceita final sem pontuação terminal
    if not cands:
        log.warning("Nenhum candidato com pontuação terminal; relaxando a regra de final de frase.")
        for i in range(len(sents)):
            words = []
            for j in range(i, len(sents)):
                words = words + sents[j]["words"]
                eff = effective_duration(words, mp, kp)
                if eff > mx:
                    break
                if eff >= mn:
                    cands.append({"s0": i, "s1": j, "start": round(words[0]["start"], 3),
                                  "end": round(words[-1]["end"], 3), "effective_duration": round(eff, 2),
                                  "topic": topics[i], "words": list(words),
                                  "text": " ".join(w["w"] for w in words)})
                    break
    return cands


def analyze(tr: dict, video: Path, meta: dict, cfg: dict) -> dict:
    ac = cfg["analysis"]
    sents = build_sentences(tr)
    if len(sents) < 3:
        log.warning("Pouquíssima fala detectada (%d frases).", len(sents))
    topics = topic_segments(sents)
    log.info("%d frases, %d assuntos detectados.", len(sents), (max(topics) + 1) if topics else 0)
    log.info("Medindo energia de áudio (streaming)…")
    energy = audio_energy(video, meta["duration"], ac["audio_window_seconds"])
    scenes = scene_changes(video, ac["scene_threshold"]) if ac["scene_detection"] else []
    n_docs = max(1, len(sents))
    df = Counter()
    for s in sents:
        df.update(set(lx.content_words(s["text"])))
    cands = build_candidates(sents, topics, cfg)
    for c in cands:
        score_candidate(c, energy, ac["audio_window_seconds"], sents, df, n_docs)
        c["scene_cuts_inside"] = sum(1 for t in scenes if c["start"] < t < c["end"])
    cands.sort(key=lambda c: -c["auto_score"])
    topic_summary = []
    for t in sorted(set(topics)):
        idx = [i for i, x in enumerate(topics) if x == t]
        txt = " ".join(sents[i]["text"] for i in idx)
        topic_summary.append({"topic": t, "start": sents[idx[0]]["start"], "end": sents[idx[-1]]["end"],
                              "keywords": keywords_for(txt, df, n_docs, 8), "n_sentences": len(idx)})
    return {"sentences": [{k: v for k, v in s.items() if k != "words"} for s in sents],
            "sentences_full": sents,
            "topics": topic_summary, "scene_changes": scenes,
            "energy_window": ac["audio_window_seconds"], "energy_db": [round(float(x), 1) for x in energy],
            "candidates": cands}


def candidates_markdown(an: dict, limit: int) -> str:
    """Resumo legível que o Claude lê para fazer a seleção editorial."""
    lines = ["# Candidatos a corte (pré-triagem heurística)", "",
             "Pontuações são heurísticas (0–100). Use como ponto de partida — a decisão editorial é sua.", "",
             "## Assuntos detectados", ""]
    for t in an["topics"]:
        lines.append(f"- T{t['topic']} [{fmt_ts(t['start'])}–{fmt_ts(t['end'])}] {', '.join(t['keywords'])}")
    lines += ["", "## Candidatos", ""]
    for i, c in enumerate(an["candidates"][:limit]):
        flags = []
        if c["profanity"]["strong"]:
            flags.append(f"palavrão forte: {','.join(c['profanity']['strong'])}")
        if c["sensitive"]:
            flags.append("sensível: " + ",".join(c["sensitive"]))
        lines.append(f"### C{i:03d} — score {c['auto_score']} | {fmt_ts(c['start'])} → {fmt_ts(c['end'])} "
                     f"| ~{c['effective_duration']}s após jump cuts | T{c['topic']}")
        lines.append(f"sentenças {c['s0']}–{c['s1']} | " + " ".join(f"{k}={v:.0f}" for k, v in c["scores"].items()))
        if flags:
            lines.append("⚠ " + " | ".join(flags))
        lines.append(f"> {c['text']}")
        lines.append("")
    return "\n".join(lines)
