"""Monetization Compliance Validator — módulo independente.

Avalia cada corte renderizado em 5 eixos (A Originalidade, B Conteúdo reutilizado/inautêntico,
C Direitos autorais, D Adequação a anunciantes, E Qualidade) usando MEDIÇÕES REAIS sobre o MP4
(ffprobe, EBU R128, blackdetect, freezedetect), o plano de edição e a declaração de direitos.

Princípios (alinhados às políticas do YPP verificadas em 11/2026 — ver references/youtube_policies.md):
* Zoom, legendas, filtros, transições e música são classificados como COSMÉTICOS e pesam pouco.
  A política de conteúdo reutilizado exige comentário original significativo, modificação
  substancial ou valor agregado; e vale MESMO com permissão do autor original.
* Direitos não verificáveis nunca resultam em BAIXO RISCO.
* A pontuação 0–100 é interna e NÃO é nota do YouTube nem probabilidade de aprovação.

Uso standalone:  python scripts/validate.py <projeto>   (revalida todos os cortes existentes)
"""
from __future__ import annotations

import re
from pathlib import Path

from . import lexicon as lx
from .audio import check_music_license
from .core import ffprobe_json, parse_rate, run

LEVELS = ["BAIXO RISCO", "RISCO MODERADO", "ALTO RISCO", "NÃO RECOMENDADO"]
COSMETIC = ["legendas dinâmicas", "zooms/movimento de câmera", "jump cuts", "transições", "trilha sonora",
            "efeitos sonoros", "reenquadramento 9:16", "barra de progresso"]


# ----------------------------------------------------------------- medições
def measure(mp4: Path) -> dict:
    info = ffprobe_json(mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", mp4, "-filter_complex",
             "[0:a]ebur128=peak=true[aout];[0:v]blackdetect=d=0.4:pix_th=0.08,freezedetect=n=0.002:d=1.5[vout]",
             "-map", "[vout]", "-map", "[aout]", "-f", "null", "-"], desc="medições do validador", check=False)
    err = p.stderr
    I = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)
    black = re.findall(r"black_duration:([\d.]+)", err)
    freeze = re.findall(r"freeze_duration: ([\d.]+)", err)
    return {"vcodec": v.get("codec_name"), "width": int(v["width"]), "height": int(v["height"]),
            "fps": round(parse_rate(v.get("avg_frame_rate")), 3), "pix_fmt": v.get("pix_fmt"),
            "acodec": a.get("codec_name") if a else None,
            "duration": float(info["format"]["duration"]), "container": info["format"]["format_name"],
            "integrated_lufs": float(I[-1]) if I else None, "true_peak_dbtp": float(tp[-1]) if tp else None,
            "black_seconds": round(sum(map(float, black)), 2), "freeze_seconds": round(sum(map(float, freeze)), 2)}


def plan_signature(plan: dict) -> tuple:
    return (plan["layout"], plan["style"], plan["variant"]["hook_anim"], plan["variant"]["intro"],
            plan["music"]["mood"], tuple(sorted({e["type"] for e in plan["editorial"]})))


def uniformity(plans: list[dict]) -> float:
    sigs = [plan_signature(p) for p in plans]
    if len(sigs) < 2:
        return 0.0
    tot, n = 0.0, 0
    for i in range(len(sigs)):
        for j in range(i + 1, len(sigs)):
            tot += sum(a == b for a, b in zip(sigs[i], sigs[j])) / len(sigs[i])
            n += 1
    return round(tot / n, 3)


def _overlap_ratio(a: str, b: str) -> float:
    wa, wb = set(lx.content_words(a)), set(lx.content_words(b))
    return len(wa & wb) / len(wa) if wa else 1.0


# ----------------------------------------------------------------- avaliação
def validate_clip(mp4: Path, plan: dict, rights: dict, cfg: dict, meta: dict, transcript_info: dict,
                  batch_uniformity: float) -> dict:
    vc = cfg["validator"]
    issues, review, recs, orig_recs, cr_risks = [], [], [], [], []
    fixes = []  # ações automáticas possíveis

    def issue(axis, sev, msg, fix=None):
        issues.append({"eixo": axis, "severidade": sev, "msg": msg})
        if fix:
            fixes.append(fix)

    m = measure(mp4) if mp4.exists() else None
    rstat = rights.get("status", "unknown")
    on_screen = bool(rights.get("creator_on_screen"))

    # ---------------- A. Originalidade / valor agregado (30)
    # Origem de cada elemento: "editor" (escrito pelo Claude/usuário), "transcricao" (gerado da fala do
    # próprio trecho), "contexto_do_video" (trazido de outra parte do vídeo). Elementos que só reorganizam
    # a fala valem menos que contexto e explicação visual; nada disso equivale a comentário novo.
    ed = plan["editorial"]
    types = [e["type"] for e in ed]
    n_edit = len(ed)
    def origin(e):
        return e.get("origin", "editor")
    copied = [e for e in ed if origin(e) == "editor" and e.get("text")
              and _overlap_ratio(e["text"], plan["text"]) > 0.8 and len(lx.content_words(e["text"])) > 3]
    a_score = 2  # edição cosmética conta, mas muito pouco
    for e in ed:
        t = e["type"]
        if t == "hook_title":
            a_score += 4 if origin(e) == "transcricao" or e in copied else 7
        elif t == "context_card":
            a_score += 7
        elif t == "fact":
            a_score += 3
        elif t == "closing":
            a_score += 3 if origin(e) == "transcricao" or e in copied else 4
    kinds = {e.get("kind", "list") for e in ed if e["type"] == "explainer"}
    a_score += min(10, 5 * len(kinds))
    a_score = max(0, min(30, a_score))
    missing_sources = [e for e in ed if e["type"] == "fact" and not e.get("source")]
    if missing_sources:
        issue("A", "alto", f"{len(missing_sources)} dado(s) complementar(es) sem fonte verificável.")
        review.append("Confirmar a fonte de cada dado exibido nos cartões antes de publicar.")
    if copied:
        issue("A", "moderado", "Textos editoriais que apenas repetem a fala do trecho — valem como destaque, não como contribuição.")
    if n_edit == 0:
        issue("A", "alto", "Nenhum elemento de edição além do cosmético "
                           "(" + ", ".join(COSMETIC[:5]) + ").")
        orig_recs.append("Gerar/revisar os elementos do editorial (título, contexto, gráficos a partir da fala, fechamento).")
    elif n_edit < 3:
        orig_recs.append("Poucos elementos de edição: acrescente contexto do vídeo ou um gráfico explicativo.")
    if not kinds:
        orig_recs.append("Nenhum gráfico explicativo: se o trecho cita números, comparações ou passos, visualize-os.")
    if "context_card" not in types:
        orig_recs.append("Sem cartão de contexto: indique o que foi dito antes do trecho para o espectador do Short.")
    if ed and all(origin(e) in ("transcricao", "contexto_do_video") for e in ed) and rstat != "own":
        issue("A", "baixo", "Todos os elementos derivam da própria fala do vídeo: reorganizam e visualizam o "
                            "conteúdo, mas não acrescentam análise nova.")
    if "hook_title" not in types:
        issue("A", "moderado", "Sem título-gancho: foi usado um rótulo automático de palavras-chave.")

    # ---------------- B. Conteúdo reutilizado / inautêntico (25)
    if rstat == "own":
        b_score = 25 if on_screen else 21
        if not on_screen:
            review.append("Confirmar que o vídeo original é do próprio canal (o YouTube avalia se o criador "
                          "pode ser claramente identificado como autor).")
    elif rstat == "licensed":
        b_score = 8 + min(14, 3 * n_edit)
        if n_edit < 3:
            issue("B", "alto", "Material licenciado de terceiro com pouca transformação: a política de conteúdo "
                               "reutilizado se aplica mesmo com permissão do autor.")
        else:
            issue("B", "moderado", "Material licenciado: a permissão não isenta da política de conteúdo reutilizado; "
                                   "a transformação aqui é visual/textual (sem comentário em voz).")
            review.append("Avaliar se a edição deixa claro o valor agregado ao material licenciado.")
    elif rstat == "unknown":
        b_score = 4 + min(8, 2 * n_edit)
        issue("B", "alto", "Titularidade do vídeo não declarada: até ser confirmada, o material é tratado como de "
                           "terceiros (risco de 'conteúdo reutilizado').")
    else:
        b_score = 2 + min(6, n_edit)
        issue("B", "alto" if n_edit >= 3 else "crítico",
              "Conteúdo de terceiros sem licença confirmada: risco de 'conteúdo reutilizado' (reupload/clipe com "
              "pouca transformação) além de risco de direitos autorais.")
    if batch_uniformity >= vc["uniformity_warn_ratio"]:
        b_score -= 4
        issue("B", "moderado", f"Os cortes do lote estão muito parecidos entre si (similaridade de template "
                               f"{batch_uniformity:.0%}) — risco de 'conteúdo inautêntico'.", fix="vary_style")
    b_score = max(0, min(25, b_score))

    # ---------------- C. Direitos autorais (20)
    c_score = 20
    if rstat == "unknown":
        c_score -= 12
        review.append("Direitos sobre o vídeo original NÃO declarados. Informe --rights own|licensed|third_party.")
        cr_risks.append("Origem e titularidade do vídeo não verificadas pela ferramenta.")
    elif rstat == "third_party":
        c_score -= 16
        cr_risks.append("Vídeo de terceiros sem licença: alta chance de reivindicação de Content ID ou remoção.")
    elif rstat == "licensed":
        if not rights.get("note"):
            c_score -= 5
            review.append("Licença declarada sem descrição: registre escopo (uso comercial no YouTube) e prova escrita.")
    ml = check_music_license(plan, cfg)
    if ml.get("status") == "biblioteca" and not ml.get("ok"):
        c_score -= 8
        issue("C", "alto", f"Trilha '{ml.get('file')}' sem licença comercial registrada em licenses.json.",
              fix="music_generated")
    for b in plan.get("broll", []):
        if not b.get("license"):
            c_score -= 4
            issue("C", "alto", f"B-roll '{b.get('file')}' sem licença registrada.")
            cr_risks.append(f"B-roll {b.get('file')} sem licença.")
    if transcript_info.get("source_has_background_audio"):
        cr_risks.append("Possível música/áudio de fundo no vídeo original (nível alto nas pausas da fala) — "
                        "pode gerar Content ID independentemente da edição.")
        review.append("Ouvir as pausas do trecho e confirmar se há música de terceiros no áudio original.")
        c_score -= 3
    c_score = max(0, c_score)

    # ---------------- D. Adequação a anunciantes (15)
    d_score = 15
    prof = lx.profanity_hits(plan["text"])
    original_texts = []  # todo texto que aparece na tela
    for e in plan["editorial"]:
        original_texts += [str(e.get(k, "")) for k in ("text", "title", "caption") if e.get(k)]
        original_texts += [str(i) for i in e.get("items", []) or []]
    ontext = " ".join(original_texts + [e.get("text", "") for e in plan["derived"]] + [plan["topic_label"]])
    prof_title = lx.profanity_hits(ontext)
    words_n = max(1, len(plan["text"].split()))
    censored = set(lx.norm(w) for w in plan.get("censor_words", []))
    strong_left = [w for w in prof["strong"] if w not in censored]
    if prof_title["strong"]:
        d_score -= 10
        issue("D", "crítico", "Palavrão forte em texto na tela/título — títulos e miniaturas são avaliados com mais rigor.",
              fix="mask_text")
    if strong_left:
        rate = len(strong_left) / words_n
        if rate > 0.03 or len(strong_left) >= 3:
            d_score -= 8
            issue("D", "alto", f"Uso frequente de palavrões fortes ({len(strong_left)} em {words_n} palavras).",
                  fix="censor_strong")
        else:
            d_score -= 2
            issue("D", "baixo", "Palavrão forte esporádico na fala (uso ocasional costuma ser aceito; confira).")
    if prof["moderate"]:
        d_score -= 1
    sens = lx.sensitive_hits(plan["text"] + " " + ontext)
    if sens:
        d_score -= min(8, 3 * len(sens))
        issue("D", "moderado", "Temas potencialmente sensíveis: " + ", ".join(f"{k} ({', '.join(v[:3])})" for k, v in sens.items()))
        review.append("Revisar o contexto dos temas sensíveis (contexto educativo/documental pode ser aceito; "
                      "detalhes gráficos limitam anúncios).")
    d_score = max(0, d_score)

    # ---------------- E. Qualidade (10)
    e_score = 10
    mn, mx = cfg["clips"]["min_duration"], cfg["clips"]["max_duration"]
    if m is None:
        issue("E", "crítico", "Arquivo do corte não existe.")
        e_score = 0
    else:
        spec_errs = []
        if m["vcodec"] != "h264":
            spec_errs.append(f"codec de vídeo {m['vcodec']}")
        if m["acodec"] != "aac":
            spec_errs.append(f"codec de áudio {m['acodec']}")
        if (m["width"], m["height"]) != (1080, 1920):
            spec_errs.append(f"resolução {m['width']}x{m['height']}")
        if spec_errs:
            e_score -= 5
            issue("E", "crítico", "Fora da especificação de exportação: " + ", ".join(spec_errs), fix="rerender")
        if not (mn - 0.05 <= m["duration"] <= mx + 0.05):
            e_score -= 4
            issue("E", "crítico", f"Duração {m['duration']:.2f}s fora de {mn}–{mx}s.", fix="fit_duration")
        if m["integrated_lufs"] is not None and abs(m["integrated_lufs"] - cfg["audio"]["target_lufs"]) > vc["lufs_tolerance"]:
            e_score -= 2
            issue("E", "moderado", f"Loudness {m['integrated_lufs']} LUFS fora do alvo {cfg['audio']['target_lufs']}.",
                  fix=("gain", cfg["audio"]["target_lufs"] - m["integrated_lufs"]))
        if m["true_peak_dbtp"] is not None and m["true_peak_dbtp"] > -0.5:
            e_score -= 1
            issue("E", "moderado", f"True peak alto ({m['true_peak_dbtp']} dBTP).", fix=("gain", -1.0))
        if m["black_seconds"] > 0.5:
            e_score -= 2
            issue("E", "moderado", f"{m['black_seconds']}s de tela preta detectados.")
            review.append("Verificar trechos pretos (podem vir do vídeo original).")
        if m["freeze_seconds"] > 3:
            e_score -= 1
            issue("E", "baixo", f"{m['freeze_seconds']}s de imagem congelada (pode ser conteúdo estático do original).")
    cap_words = sum(len(g["words"]) for g in plan["captions"])
    if cap_words < 0.9 * words_n:
        e_score -= 2
        issue("E", "moderado", "Legendas não cobrem toda a fala.", fix="rerender")
    if transcript_info.get("word_timing") == "estimated":
        e_score -= 1
        review.append("Tempos por palavra foram ESTIMADOS (transcrição importada sem timestamps por palavra): "
                      "confira a sincronia das legendas.")
    lp = transcript_info.get("clip_avg_logprob")
    if lp is not None and lp < -0.8:
        e_score -= 2
        review.append("Confiança baixa da transcrição neste trecho — revise o texto das legendas.")
    src_crop_h = plan.get("_source_crop_height")
    if src_crop_h and src_crop_h < vc["min_crop_source_height_warn"]:
        e_score -= 1
        issue("E", "baixo", f"Imagem ampliada a partir de ~{src_crop_h}px de altura: pode perder nitidez.")
    if any(e.get("_omitted") for e in plan["editorial"]):
        issue("E", "baixo", "Algum elemento editorial não coube na timeline e foi omitido.")
    e_score = max(0, e_score)

    score = int(round(a_score + b_score + c_score + d_score + e_score))
    sev = [i["severidade"] for i in issues]
    if "crítico" in sev or score < 40:
        level = 3
    elif "alto" in sev or score < 60:
        level = 2
    elif "moderado" in sev or score < 80 or review:
        level = 1
    else:
        level = 0
    if rstat in ("unknown",) and level < 1:
        level = 1
    if rstat == "third_party" and level < 2:
        level = 2
    if rstat == "licensed" and n_edit < 3 and level < 2:
        level = 2
    for i in issues:
        if i["eixo"] == "A" and i["severidade"] == "alto":
            recs.append("Sem contribuição editorial, este corte tende a ser visto como reaproveitamento. "
                        "Acrescente contexto/explicação originais antes de publicar.")
            break
    if level >= 2:
        recs.append("Não publicar na forma atual sem resolver os itens de severidade alta/crítica.")
    recs.append("A classificação é técnica e preventiva — não garante aprovação nem monetização pelo YouTube.")
    motivos = [f"Direitos declarados: '{rstat}'" + (" (criador aparece no vídeo)" if on_screen else "")]
    worst = [i for i in issues if i["severidade"] in ("crítico", "alto")]
    if worst:
        motivos += [f"Severidade {i['severidade']}: {i['msg']}" for i in worst]
    elif any(i["severidade"] == "moderado" for i in issues):
        motivos.append("Há problemas moderados (ver lista).")
    if review:
        motivos.append(f"{len(set(review))} item(ns) dependem de revisão humana — por regra, isso impede 'BAIXO RISCO'.")
    if rstat == "unknown":
        motivos.append("Direitos não verificáveis: classificação limitada a no mínimo RISCO MODERADO.")
    if rstat == "third_party":
        motivos.append("Terceiros sem licença: classificação limitada a no mínimo ALTO RISCO.")
    if not worst and not review and not issues:
        motivos.append("Nenhum problema relevante identificado nos critérios verificáveis.")
    return {
        "id": plan["id"], "classificacao": LEVELS[level], "motivos": motivos, "nivel": level, "pontuacao_interna": score,
        "pontuacao_por_eixo": {"A_originalidade": a_score, "B_reutilizado": b_score, "C_direitos": c_score,
                               "D_anunciantes": d_score, "E_qualidade": e_score},
        "problemas": issues, "revisao_humana": sorted(set(review)), "recomendacoes": recs,
        "melhorias_originalidade": orig_recs, "riscos_direitos_autorais": cr_risks,
        "elementos_originais": [e["type"] for e in ed], "elementos_cosmeticos": COSMETIC,
        "medicoes": m, "acoes_autofix": fixes, "seguro": level == 0,
    }
