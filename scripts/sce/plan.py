"""Etapa 5 — estratégia de edição individual de cada corte.

Combina: seleção + editorial.json (contribuição original escrita pelo Claude/usuário) + sinais
técnicos (rostos, energia, pausas). O resultado (analise/plans/corte_XX.json) descreve tudo que o
renderizador fará: intervalos mantidos (jump cuts), layout, movimentos de câmera, cartões,
legendas, música e efeitos. O validador lê o mesmo plano para saber o que é original e o que é
apenas cosmético.
"""
from __future__ import annotations

import hashlib
import re

from . import lexicon as lx
from .core import log

# Presets de identidade visual. Mantêm uma família visual comum (cores-base do projeto)
# mas variam tipografia, posição, animação e tratamento da legenda.
STYLES = {
    "energetico": {"font": "Inter Black", "caption_size": 92, "uppercase": True, "primary": "FFFFFF",
                   "highlight": "FFD400", "keyword": "00E5FF", "outline": 7, "box": False, "words_per_group": 2,
                   "pop": True, "music": "energetico", "camera": "punchy", "card_bg": (12, 12, 16, 225),
                   "accent": (255, 212, 0)},
    "educativo": {"font": "Poppins", "caption_size": 74, "uppercase": False, "primary": "FFFFFF",
                  "highlight": "7CFFB2", "keyword": "FFD400", "outline": 0, "box": True, "words_per_group": 4,
                  "pop": False, "music": "educativo", "camera": "calm", "card_bg": (16, 24, 40, 230),
                  "accent": (124, 255, 178)},
    "historia": {"font": "Poppins", "caption_size": 80, "uppercase": False, "primary": "F5F0E6",
                 "highlight": "FF9F43", "keyword": "FFFFFF", "outline": 5, "box": False, "words_per_group": 3,
                 "pop": False, "music": "historia", "camera": "drift", "card_bg": (30, 20, 12, 220),
                 "accent": (255, 159, 67)},
    "debate": {"font": "Inter ExtraBold", "caption_size": 84, "uppercase": True, "primary": "FFFFFF",
               "highlight": "FF3B5C", "keyword": "FFD400", "outline": 6, "box": False, "words_per_group": 3,
               "pop": True, "music": "debate", "camera": "punchy", "card_bg": (24, 8, 14, 225),
               "accent": (255, 59, 92)},
    "humor": {"font": "Poppins", "caption_size": 88, "uppercase": False, "primary": "FFFFFF",
              "highlight": "FF7AE0", "keyword": "FFE14D", "outline": 7, "box": False, "words_per_group": 2,
              "pop": True, "music": "humor", "camera": "bouncy", "card_bg": (30, 10, 40, 220),
              "accent": (255, 122, 224)},
}
STYLE_ORDER = list(STYLES)


def guess_style(text: str) -> str:
    toks = lx.tokens(text)
    if any(m in toks for m in lx.HUMOR_MARKERS):
        return "humor"
    if sum(1 for m in lx.OPINION_MARKERS if m in toks) >= 2:
        return "debate"
    if re.search(r"\d", text) or any(t in toks for t in ("como", "passo", "porque", "funciona", "how", "why")):
        return "educativo"
    if any(t in toks for t in ("quando", "dia", "lembro", "aconteceu", "historia", "when", "story")):
        return "historia"
    return "energetico"


def kept_intervals(words: list[dict], cfg: dict, fps: float) -> list[list[float]]:
    jc = cfg["jump_cuts"]
    lead, tail = 0.15, 0.28
    if not jc["enabled"]:
        return [[words[0]["start"] - lead, words[-1]["end"] + tail]]
    mp, kp = jc["min_pause_seconds"], jc["keep_pause_seconds"]
    iv = [[max(0.0, words[0]["start"] - lead), None]]
    for a, b in zip(words, words[1:]):
        if b["start"] - a["end"] > mp:
            iv[-1][1] = a["end"] + kp / 2
            iv.append([b["start"] - kp / 2, None])
    iv[-1][1] = words[-1]["end"] + tail
    q = 1.0 / fps
    out = []
    for a, b in iv:  # alinha a quadros para manter áudio e vídeo sincronizados
        a, b = round(a / q) * q, round(b / q) * q
        if b - a >= 2 * q:
            out.append([round(a, 4), round(b, 4)])
    return out


def to_output_time(t: float, iv: list[list[float]]) -> float | None:
    acc = 0.0
    for a, b in iv:
        if t < a:
            return acc
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return acc


def fit_duration(iv: list[list[float]], cfg: dict, src_dur: float) -> list[list[float]]:
    """Garante 20–25 s no resultado final, ajustando respiros nas bordas."""
    mn, mx = cfg["clips"]["min_duration"], cfg["clips"]["max_duration"]
    total = sum(b - a for a, b in iv)
    if total < mn:
        need = mn - total + 0.05
        add_tail = min(need, max(0.0, src_dur - iv[-1][1]), 1.2)
        iv[-1][1] += add_tail
        need -= add_tail
        if need > 0:
            add_head = min(need, iv[0][0], 1.0)
            iv[0][0] -= add_head
    total = sum(b - a for a, b in iv)
    if total > mx:
        iv[-1][1] -= min(total - mx + 0.02, 0.25)
    return iv


def caption_groups(words: list[dict], iv: list[list[float]], per_group: int, keywords: set[str]) -> list[dict]:
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        brk = len(cur) >= per_group or re.search(r"[.,!?;:…]$", w["w"]) or (nxt and nxt["start"] - w["end"] > 0.35)
        if brk or nxt is None:
            groups.append(cur)
            cur = []
    out = []
    for g in groups:
        items = []
        for w in g:
            s, e = to_output_time(w["start"], iv), to_output_time(w["end"], iv)
            norm = lx.norm(re.sub(r"[^\w]", "", w["w"]))
            items.append({"w": w["w"], "start": round(s, 3), "end": round(max(e, s + 0.08), 3),
                          "key": norm in keywords or norm in lx.EMPHASIS_WORDS or bool(re.search(r"\d", w["w"]))})
        out.append({"start": items[0]["start"], "end": items[-1]["end"], "words": items})
    for a, b in zip(out, out[1:]):  # evita lacunas piscando entre grupos próximos
        if b["start"] - a["end"] < 0.25:
            a["end"] = b["start"]
    return out


def camera_keyframes(groups: list[dict], iv: list[list[float]], style: dict, duration: float, cc: dict) -> dict:
    """Zooms de ênfase SUAVES e espaçados nas palavras-chave. Sem trocas bruscas de enquadramento nos
    jump cuts: a continuidade vem do dissolve e de um push-in lento ao longo do corte."""
    punches = []
    last = -10
    for g in groups:
        for w in g["words"]:
            if w["key"] and w["start"] - last > cc["punch_min_spacing"] and 1.2 < w["start"] < duration - 2.0:
                punches.append({"t": round(w["start"] - 0.25, 3), "amount": cc["punch_amount"]})
                last = w["start"]
    cuts, acc = [], 0.0
    for a, b in iv[:-1]:
        acc += b - a
        cuts.append(round(acc, 3))
    return {"mode": style["camera"], "punches": punches, "jump_cuts": cuts,
            "push_in": cc["push_in"], "duration": duration}


def choose_layout(content_type: str, face: dict, facecam: dict | None, meta: dict) -> str:
    """Prioridade: reação → tela dividida; pessoa falando → rosto; resto → conteúdo."""
    if meta["height"] >= meta["width"]:
        return "fill"
    if facecam and content_type in ("reacao", "auto"):
        return "reaction"
    ratio = face.get("face_ratio") or 0
    big_face = (face.get("face_size_mean") or 0) >= 0.18
    if content_type in ("podcast", "entrevista", "vlog") and ratio >= 0.3:
        return "face"
    if content_type == "auto" and ratio >= 0.5 and big_face:
        return "face"
    return "content"


def _mask_profanity(text: str) -> str:
    def rep(m):
        w = m.group(0)
        return w[0] + "*" * (len(w) - 1) if lx.norm(w) in lx.PROFANITY_STRONG else w
    return re.sub(r"\w+", rep, text)


def build_plan(idx: int, clip: dict, editorial: dict, face: dict, meta: dict, cfg: dict,
               style_override: str | None = None, variant_override: dict | None = None,
               content_type: str = "auto", facecam: dict | None = None) -> dict:
    fps = meta["render_fps"]
    ed = editorial or {}
    style_name = style_override or ed.get("style") or guess_style(clip["text"])
    if style_name not in STYLES:
        log.warning("Estilo '%s' desconhecido; usando energetico.", style_name)
        style_name = "energetico"
    style = STYLES[style_name]
    iv = fit_duration(kept_intervals(clip["words"], cfg, fps), cfg, meta["duration"])
    duration = round(sum(b - a for a, b in iv), 3)
    kw = set(lx.norm(k) for k in (ed.get("keywords") or clip.get("keywords") or []))
    groups = caption_groups(clip["words"], iv, style["words_per_group"], kw)
    layout = ed.get("layout") or choose_layout(content_type, face, facecam, meta)
    if layout == "blurfill":
        layout = "content"
    if layout == "reaction" and not facecam:
        log.warning("Corte %02d: layout de reação pedido mas a facecam não foi localizada — usando 'content'.", idx)
        layout = "content"
    # ---- elementos editoriais (originais) vs derivados ----
    # origin: "editor" = escrito pelo Claude/usuário; "transcricao" = gerado de forma automática a partir da fala
    origin = ed.get("_origin", "editor")
    editorial_elements = []
    hook = ed.get("hook_title")
    if hook:
        editorial_elements.append({"type": "hook_title", "text": _mask_profanity(hook), "origin": origin})
    ctx = ed.get("context_card")
    if ctx and ctx.get("text"):
        editorial_elements.append({"type": "context_card", "text": _mask_profanity(ctx["text"]),
                                   "label": ctx.get("label", "Contexto"), "origin": ctx.get("origin", origin),
                                   "at": float(ctx.get("at", 3.4)), "duration": float(ctx.get("duration", 4.0))})
    for f in ed.get("facts", []) or []:
        editorial_elements.append({"type": "fact", "text": _mask_profanity(f["text"]), "source": f.get("source"),
                                   "origin": "editor",
                                   "at": float(f.get("at", duration * 0.55)), "duration": float(f.get("duration", 3.5))})
    exs = list(ed.get("explainers") or [])
    if ed.get("explainer"):
        exs.insert(0, ed["explainer"])
    for ex in exs:
        ex = dict(ex)
        if "at_src" in ex:  # tempo na fala original → tempo no corte (após jump cuts)
            ex["at"] = round(max(0.0, to_output_time(float(ex.pop("at_src")), iv) - 0.15), 2)
        ex.setdefault("at", duration * 0.45)
        ex.setdefault("duration", 3.6)
        ex.setdefault("origin", origin)
        ex.pop("type", None)
        for k in ("title", "caption"):
            if ex.get(k):
                ex[k] = _mask_profanity(ex[k])
        if ex.get("items"):
            ex["items"] = [_mask_profanity(str(i)) for i in ex["items"]]
        editorial_elements.append({"type": "explainer", **ex})
    if ed.get("closing_text"):
        editorial_elements.append({"type": "closing", "text": _mask_profanity(ed["closing_text"]), "origin": origin,
                                   "at": max(0.0, duration - 2.4), "duration": 2.4})
    derived = []
    if not hook:  # fallback: rótulo derivado das palavras-chave (NÃO conta como original)
        label = clip.get("title") or " ".join((clip.get("keywords") or ["destaque"])[:3]).upper()
        derived.append({"type": "hook_title", "text": _mask_profanity(label), "derived": True})
    topic_label = ed.get("topic_label") or clip.get("topic_label") or \
        (clip.get("keywords") or ["destaque"])[0].capitalize()
    # variação determinística (evita template idêntico entre cortes)
    seed = int(hashlib.md5(f"{idx}{clip['start']}".encode()).hexdigest()[:8], 16)
    variant = {"hook_anim": ["fade_up", "fade_scale", "fade_up"][seed % 3],
               "card_side": ["left", "right"][(seed >> 3) % 2],
               "intro": "fade",
               "progress_bar": ed.get("progress_bar", True)}
    if variant_override:
        variant.update(variant_override)
    broll = ed.get("broll", []) or []
    plan = {
        "index": idx, "id": f"{idx:02d}", "source_start": clip["start"], "source_end": clip["end"],
        "intervals": iv, "duration": duration, "fps": fps, "style": style_name, "style_def": style,
        "layout": layout, "face": face, "camera": camera_keyframes(groups, iv, style, duration, cfg["camera"]),
        "content_type": content_type, "facecam": facecam if layout == "reaction" else None,
        "captions": groups, "keywords": sorted(kw), "topic_label": _mask_profanity(topic_label),
        "editorial": editorial_elements, "derived": derived, "variant": variant, "broll": broll,
        "music": {"enabled": cfg["audio"]["music_enabled"] and ed.get("music", True) is not False,
                  "mood": ed.get("music_mood") or style["music"], "source": cfg["audio"]["music_source"],
                  "file": ed.get("music_file"), "license": ed.get("music_license")},
        "sfx": cfg["audio"]["sfx_enabled"],
        "censor_words": ed.get("censor_words", []),
        "text": clip["text"], "justification": clip.get("justification", ""),
        "title": ed.get("hook_title") or clip.get("title") or topic_label,
        "selection_source": clip.get("source", "auto"), "scores": clip.get("scores", {}),
        "auto_score": clip.get("auto_score"),
    }
    return plan
