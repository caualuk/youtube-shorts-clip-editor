"""Elementos de edição gerados a partir da TRANSCRIÇÃO (sem narração, sem fatos externos).

A ideia: entender o que o trecho diz e transformar isso em camadas visuais que ajudam a compreender,
sincronizadas com a fala:

* título-gancho  — pergunta/afirmação mais forte do trecho, encurtada na fronteira de oração;
* contexto       — o que foi dito ANTES do trecho no vídeo (o espectador do Short não viu isso);
* contador       — números falados ("cem mil reais", "30 dias") viram número animado no momento da fala;
* contraste      — estruturas "não é X, é Y" / "nunca foi X, foi Y" viram um cartão ✗ X / ✓ Y;
* pontos-chave   — 2–3 orações centrais do argumento, em lista progressiva;
* fechamento     — a conclusão do trecho (ou uma pergunta, se o trecho é opinativo).

Nada é inventado: todo texto vem da fala (encurtado), e cada elemento guarda a origem para o validador.
O Claude pode revisar/reescrever tudo em analise/editorial.json; o que estiver lá tem prioridade.
"""
from __future__ import annotations

import re

from . import lexicon as lx
from .plan import guess_style

# ------------------------------------------------------------------ números por extenso (pt-BR)
_UNI = {"zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
        "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14,
        "quatorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19,
        "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70,
        "oitenta": 80, "noventa": 90, "cem": 100, "cento": 100, "duzentos": 200, "duzentas": 200,
        "trezentos": 300, "quatrocentos": 400, "quinhentos": 500, "seiscentos": 600, "setecentos": 700,
        "oitocentos": 800, "novecentos": 900}
_MULT = {"mil": 1000, "milhao": 10 ** 6, "milhoes": 10 ** 6, "bilhao": 10 ** 9, "bilhoes": 10 ** 9}
UNITS = {"reais": ("R$ ", ""), "real": ("R$ ", ""), "dolares": ("US$ ", ""), "por cento": ("", "%"),
         "porcento": ("", "%"), "%": ("", "%"), "anos": ("", " anos"), "dias": ("", " dias"),
         "horas": ("", " horas"), "minutos": ("", " minutos"), "semanas": ("", " semanas"),
         "meses": ("", " meses"), "pessoas": ("", " pessoas"), "livros": ("", " livros"),
         "vezes": ("", "x"), "segundos": ("", " s"), "quilos": ("", " kg"), "km": ("", " km")}
SKIP_SMALL = {1, 2}  # "um", "dois" isolados raramente são dados


def find_numbers(words: list[dict]) -> list[dict]:
    """Encontra números falados (dígitos ou por extenso) + unidade seguinte."""
    toks = [lx.norm(re.sub(r"[^\w%,.]", "", w["w"])).strip(".,") for w in words]
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        val, j = None, i
        if re.fullmatch(r"\d+([.,]\d+)?", t):
            val = float(t.replace(".", "").replace(",", ".")) if "," in t or len(t.split(".")[-1]) != 3 \
                else float(t.replace(".", ""))
            j = i + 1
            if j < len(toks) and toks[j] in _MULT:
                val *= _MULT[toks[j]]
                j += 1
        elif t in _UNI or t in _MULT:
            total, cur, j = 0, 0, i
            while j < len(toks):
                x = toks[j]
                if x in _UNI:
                    cur += _UNI[x]
                elif x in _MULT:
                    cur = max(cur, 1) * _MULT[x]
                    total += cur
                    cur = 0
                elif x == "e" and j + 1 < len(toks) and (toks[j + 1] in _UNI or toks[j + 1] in _MULT) and j > i:
                    pass
                else:
                    break
                j += 1
            val = total + cur
        if val is None:
            i += 1
            continue
        unit = None
        if j < len(toks):
            nxt2 = " ".join(toks[j:j + 2])
            if nxt2 in UNITS:
                unit, j = nxt2, j + 2
            elif toks[j] in UNITS:
                unit, j = toks[j], j + 1
            elif toks[j] == "de" and j + 1 < len(toks) and toks[j + 1] in UNITS:
                unit, j = toks[j + 1], j + 2
        out.append({"value": val, "unit": unit, "i0": i, "i1": j, "start": words[i]["start"]})
        i = max(j, i + 1)
    return out


# ------------------------------------------------------------------ orações
_LEAD = [lx.norm(x) for x in ["e ", "mas ", "entao ", "agora ", "ou seja, ", "ou seja ", "eu acho que ", "eu acredito que ",
                              "a verdade e que ", "o segredo e ", "porque ", "e aqui vai ", "sabe ", "olha ",
                              "desde entao ", "por fim, ", "depois "]]


def _clean_lead(text: str) -> str:
    t = text.strip()
    changed = True
    while changed:
        changed = False
        for lead in _LEAD:
            if lx.norm(t).startswith(lead) and len(t) > len(lead) + 8:
                t = t[len(lead):].lstrip(" ,")
                changed = True
    return t[:1].upper() + t[1:]


def clauses(sentence: str) -> list[str]:
    parts = re.split(r"[,;:]\s+|\s+(?:mas|porém|porque)\s+", sentence.strip().rstrip(".!?…"))
    return [p.strip() for p in parts if p.strip()]


def short_clause(sentence: str, max_words: int = 8, min_words: int = 3) -> str | None:
    s = _clean_lead(sentence.rstrip(".!?…"))
    if min_words <= len(s.split()) <= max_words and _ok_phrase(s):
        return s
    for c in clauses(sentence):
        c = _clean_lead(c)
        n = len(c.split())
        if min_words <= n <= max_words and _ok_phrase(c):
            return c
    return None


_BAD_START = set(lx.norm(w) for w in "se como que quando onde na no nas nos de do da em para pra com por sem ate ao aos "
                 "isso isto esse essa ele ela eu tambem".split())
_BAD_END = set(lx.norm(w) for w in "e de do da em no na que o a os as um uma para com por se mais muito foi é sao".split())
_VERBISH = re.compile(r"(ar|er|ir|ava|avam|ou|ia|iam|ei|amos|emos|imos|ando|endo|indo)$")
_INTRO = re.compile(r"\b(vamos falar|quero falar|quero contar|mudando .{0,20}assunto|vamos para|falando de|sobre o|"
                    r"o assunto|por fim|uma última coisa|agora uma)\b", re.I)


def _ok_phrase(c: str) -> bool:
    w = c.split()
    if not w:
        return False
    first, last = lx.norm(re.sub(r"[^\w]", "", w[0])), lx.norm(re.sub(r"[^\w]", "", w[-1]))
    return first not in _BAD_START and last not in _BAD_END


def surface_map(words: list[dict]) -> dict:
    m = {}
    for w in words:
        raw = re.sub(r"[^\w-]", "", w["w"])
        if raw:
            m.setdefault(lx.norm(raw), raw)
    return m


_TOPIC_RX = re.compile(r"\b(?:falar|falando|vamos para|sobre|assunto)\s+(?:de |do |da |dos |das |sobre )?"
                       r"(?:a |o |as |os )?(?:ciência d[oa] )?([A-Za-zÀ-ÿ]{4,})", re.I)


def topic_label(text: str, words: list[dict], kws: list[str], intro_text: str = "") -> str:
    for src in (text, intro_text):
        m = _TOPIC_RX.search(src or "")
        if m and lx.norm(m.group(1)) not in lx.STOPWORDS and not _VERBISH.search(lx.norm(m.group(1))):
            return m.group(1).capitalize()
    # substantivos tendem a vir depois de artigo/preposição ("o hábito", "da manhã")
    sm = surface_map(words)
    toks = lx.tokens(text)
    dets = {"o", "a", "os", "as", "do", "da", "dos", "das", "no", "na", "um", "uma", "seu", "sua", "meu", "minha"}
    best = None
    for k in kws:
        if len(k) < 4 or k in lx.STOPWORDS or k.isdigit() or k in _UNI or k in _MULT:
            continue
        det_hits = sum(1 for i in range(1, len(toks)) if toks[i] == k and toks[i - 1] in dets)
        sc = det_hits * 3 + toks.count(k)
        if det_hits and (best is None or sc > best[0]):
            best = (sc, k)
    if best is None:
        for i in range(1, len(toks)):
            if toks[i - 1] in dets and len(toks[i]) >= 5 and toks[i] not in lx.STOPWORDS:
                best = (0, toks[i])
                break
    k = best[1] if best else (kws[0] if kws else "destaque")
    return sm.get(k, k).capitalize()


def _density(text: str) -> float:
    toks = lx.tokens(text)
    return len(lx.content_words(text)) / max(1, len(toks))


# ------------------------------------------------------------------ contraste
_CONTRAST = [
    re.compile(r"n[ãa]o (?:é|foi|são|era|está) ([^,.;]+?),\s*(?:mas |e sim )?(?:é|foi|são|era|está) ([^,.;!?]+)", re.I),
    re.compile(r"nunca (?:foi|é|são) ([^,.;]+?),\s*(?:sempre )?(?:foi|é|são) ([^,.;!?]+)", re.I),
    re.compile(r"n[ãa]o porque ([^,.;]+?),\s*mas porque ([^,.;!?]+)", re.I),
    re.compile(r"n[ãa]o (?:é|são) ([^,.;]+?),\s*mas ([^,.;!?]+)", re.I),
]


def _trim_words(s: str, n: int = 7) -> str:
    w = s.strip().split()
    if len(w) <= n:
        return " ".join(w)
    w = w[:n]
    while len(w) > 2 and (lx.norm(w[-1]) in lx.STOPWORDS or lx.norm(w[-1]) in _BAD_END):
        w.pop()
    return " ".join(w) + "…"


def find_contrast(clip_sents: list[dict]) -> dict | None:
    for s in clip_sents:
        for rx in _CONTRAST:
            m = rx.search(s["text"])
            if m:
                x, y = _trim_words(m.group(1)), _trim_words(m.group(2))
                if 1 <= len(x.split()) and 2 <= len(y.split()):
                    subj = s["text"][:m.start()].strip(" ,")
                    subj = _clean_lead(subj) if 1 <= len(subj.split()) <= 3 else ""
                    title = (subj + ": não é isso…") if subj else "Não é isso…"
                    full_y = " ".join(m.group(2).split()[:10])
                    return {"kind": "contrast", "title": title, "items": [x, y], "at_src": s["start"],
                            "_subject": subj, "_full_y": full_y}
    return None


# ------------------------------------------------------------------ montagem
def _fmt_value(v: float) -> float:
    return int(v) if float(v).is_integer() else round(v, 1)


def build_auto_editorial(clip: dict, sents: list[dict], topics: list[dict], used_hooks: set) -> dict:
    s0, s1 = clip["s0"], clip["s1"]
    clip_sents = [{"text": " ".join(w["w"] for w in sents[i]["words"]), "start": sents[i]["start"],
                   "end": sents[i]["end"], "words": sents[i]["words"]} for i in range(s0, s1 + 1)]
    text = clip["text"]
    kws = clip.get("keywords") or lx.content_words(text)[:5]
    ed = {"_origin": "transcricao", "style": guess_style(text), "keywords": kws[:6]}
    # ---- título-gancho: abertura forte do trecho (perguntas e afirmações completas têm prioridade)
    cands = []
    for k, s in enumerate(clip_sents):
        t = s["text"].strip()
        tl = lx.tokens(t)
        sc = (25 if t.endswith("?") else 0) + 10 * sum(1 for m in lx.HOOK_MARKERS if m in tl) + \
            (8 if re.search(r"\d", t) else 0) + (15 if k == 0 else 0) - 5 * k
        body = _clean_lead(t.rstrip(".!…"))
        for lead in ("Você sabia que ", "Sabia que "):
            if body.startswith(lead) and len(body.split()) > 6:
                body = body[len(lead):]
                body = body[:1].upper() + body[1:]
        if re.search(r"\b(vamos falar|vamos para|quero falar|quero contar|mudando .{0,20}assunto)\b", t, re.I):
            sc -= 40
        limit = 12 if body.endswith("?") else 11
        if 3 <= len(body.split()) <= limit and _ok_phrase(body.rstrip("?")):
            cands.append((sc, body))
        else:
            c = short_clause(t, 8, 3)
            if c:
                cands.append((sc - 10, c))
    cands.sort(key=lambda x: -x[0])
    hook = next((c for _, c in cands if lx.norm(c) not in used_hooks), None)
    if hook:
        ed["hook_title"] = hook
        used_hooks.add(lx.norm(hook))
    # ---- rótulo do assunto
    # ---- contexto: o que veio antes no vídeo (introdução do assunto ou frase mais relacionada)
    kwset = set(kws)
    best, best_sc = None, 0.0
    for back, idx in enumerate(range(s0 - 1, max(-1, s0 - 11), -1)):
        st = sents[idx]
        t = " ".join(w["w"] for w in st["words"])
        if not 5 <= len(t.split()) <= 24:
            continue
        ov = len(set(lx.content_words(t)) & kwset)
        intro = bool(_INTRO.search(t)) and back <= 2
        if not (ov or intro):
            continue
        sc = ov * 2 + (4 if intro else 0) + _density(t) - back * 0.3
        if sc > best_sc:
            best, best_sc = t, sc
    inside_intro = any(_INTRO.search(cs["text"]) for cs in clip_sents[:1])
    ed["topic_label"] = topic_label(text, clip["words"], kws, "" if inside_intro else (best or ""))
    if best and not inside_intro:
        ed["context_card"] = {"label": "Antes deste trecho", "text": f"“{_clean_lead(best).rstrip('.')}.”",
                              "origin": "contexto_do_video"}
    # ---- elementos explicativos sincronizados com a fala
    exs = []
    nums = [n for n in find_numbers(clip["words"]) if not (n["value"] in SKIP_SMALL and not n["unit"])]
    nums = [n for n in nums if n["unit"] or n["value"] >= 100]
    if nums:
        n = max(nums, key=lambda n: (n["unit"] is not None, n["value"]))
        pre, suf = UNITS.get(n["unit"], ("", "")) if n["unit"] else ("", "")
        # legenda do número: o complemento logo após ("… com juros compostos") se for curto;
        # senão, a oração que leva até ele ("Minha produção dobrou em…")
        ws = clip["words"]
        k1 = n["i1"]
        after = []
        while k1 < len(ws) and len(after) < 5:
            after.append(ws[k1]["w"])
            if re.search(r"[,.;:!?]$", ws[k1]["w"]):
                break
            k1 += 1
        after_txt = " ".join(after).strip(" ,.;:!?")
        first_after = lx.norm(after[0]) if after else ""
        if first_after not in ("e", "mas", "ou", "que") and 2 <= len(after_txt.split()) <= 4 and re.search(r"[,.;:!?]$", after[-1] if after else ""):
            cap = after_txt
        else:
            k0 = n["i0"]
            while k0 > 0 and not re.search(r"[,.;:!?]$", ws[k0 - 1]["w"]) and n["i0"] - k0 < 6:
                k0 -= 1
            before_w = [w["w"] for w in ws[k0:n["i0"]]]
            if n["i0"] - k0 >= 6:  # oração longa demais: começa num verbo/artigo plausível
                while before_w and lx.norm(re.sub(r"[^\w]", "", before_w[0])) not in \
                        {"pode", "podem", "minha", "meu", "a", "o", "quem", "eu", "voce", "dormir", "leio"} and len(before_w) > 3:
                    before_w.pop(0)
            cap = " ".join(before_w).strip(" ,.")
            cap = _clean_lead(cap) + "…" if len(cap.split()) >= 2 else ""
        exs.append({"kind": "stat", "title": "Em números", "value": _fmt_value(n["value"]), "prefix": pre,
                    "suffix": suf, "caption": cap[:44], "at_src": n["start"], "duration": 3.2})
    con = find_contrast(clip_sents)
    if con:
        exs.append(dict(con, duration=3.6))
    if len(exs) < 2:
        pts = []
        for s in clip_sents:
            c = short_clause(s["text"], 6, 3)
            if c and _density(c) >= 0.4 and lx.norm(c) != lx.norm(ed.get("hook_title", "")):
                pts.append((s["start"], c))
        if len(pts) >= 2:
            pts = pts[:3]
            exs.append({"kind": "list", "title": "Pontos-chave", "items": [p for _, p in pts],
                        "at_src": pts[0][0], "duration": 4.0})
    if exs:
        ed["explainers"] = exs
    # ---- fechamento: conclusão do próprio trecho; pergunta só quando o trecho é opinativo
    closing = None
    if con and con.get("_subject"):
        y = con.get("_full_y") or con["items"][1]
        closing = f"{con['_subject']}: {y[:1].lower() + y[1:]}"
    if not closing:
        concl = [s for s in clip_sents if re.search(r"\b(ent[ãa]o|ou seja|por isso|o segredo|regra|o problema|"
                                                   r"a verdade|comece|escolha|o maior)\b", s["text"], re.I)]
        for src in reversed(concl or clip_sents[-1:]):
            c = short_clause(src["text"], 9, 4)
            if c and lx.norm(c) != lx.norm(ed.get("hook_title", "")):
                closing = c
                break
    toks = lx.tokens(text)
    if sum(1 for m in lx.OPINION_MARKERS if m in toks) >= 2 or not closing:
        qs = ["Você concorda?", "Faz sentido pra você?", "Qual é a sua opinião?", "Você faria diferente?"]
        if ed["style"] == "historia":
            qs = ["Já passou por isso?", "O que você teria feito?"]
        closing = qs[clip["s0"] % len(qs)]
    ed["closing_text"] = closing
    for x in ed.get("explainers", []):
        x.pop("_subject", None)
        x.pop("_full_y", None)
    return ed
