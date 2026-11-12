"""Legendas dinâmicas em ASS (renderizadas pelo libass no FFmpeg).

* Grupos curtos de palavras sincronizados por palavra;
* palavra ativa destacada (cor + escala), palavras-chave com cor própria;
* animação de entrada (pop com escala elástica ou fade/slide), conforme o estilo;
* quebra inteligente em até 2 linhas; caixa de fundo opcional (estilo educativo);
* posição ajustada quando há cartão editorial na tela, para nunca sobrepor.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import lexicon as lx


def _t(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _bgr(hexrgb: str) -> str:
    r, g, b = hexrgb[0:2], hexrgb[2:4], hexrgb[4:6]
    return f"&H00{b}{g}{r}&".upper()


def _esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def _split_lines(words: list[str], max_chars: int) -> list[list[int]]:
    total = sum(len(w) for w in words) + len(words) - 1
    if total <= max_chars or len(words) < 2:
        return [list(range(len(words)))]
    best, best_diff = 1, 1e9
    for k in range(1, len(words)):
        a = sum(len(w) for w in words[:k]) + k - 1
        b = total - a - 1
        if abs(a - b) < best_diff:
            best, best_diff = k, abs(a - b)
    return [list(range(best)), list(range(best, len(words)))]


def build_ass(plan: dict, out: Path, width: int, height: int, caption_y: int) -> int:
    st = plan["style_def"]
    size = st["caption_size"]
    primary, hl, kw = _bgr(st["primary"]), _bgr(st["highlight"]), _bgr(st["keyword"])
    if st["box"]:
        border_style, outline, back = 3, 14, "&H70101010&"
    else:
        border_style, outline, back = 1, st["outline"], "&H96000000&"
    base_y = caption_y
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{size},{primary},{primary},&H00000000&,{back},-1,0,0,0,100,100,1,0,{border_style},{outline},3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    censor = set(lx.norm(w) for w in plan.get("censor_words", []))
    max_chars = 16 if st["uppercase"] else 20
    for g in plan["captions"]:
        ws = g["words"]
        disp = []
        for w in ws:
            txt = _esc(w["w"])
            if lx.norm(re.sub(r"[^\w]", "", txt)) in censor:
                txt = txt[0] + "*" * (len(txt) - 1)
            disp.append(txt.upper() if st["uppercase"] else txt)
        rows = _split_lines(disp, max_chars)
        mid = (g["start"] + g["end"]) / 2
        y = base_y
        for k, w in enumerate(ws):
            start = w["start"] if k > 0 else g["start"]
            end = ws[k + 1]["start"] if k + 1 < len(ws) else g["end"]
            if end - start < 0.04:
                continue
            parts = []
            for row in rows:
                seg = []
                for j in row:
                    col = hl if j == k else (kw if ws[j]["key"] else primary)
                    scale = 104 if ws[j]["key"] else 100  # destaque por cor; escala quase constante = fluido
                    # no 1º evento do grupo a escala fica com a animação de entrada (\t global)
                    stag = "" if (k == 0 or st["box"]) else f"\\fscx{scale}\\fscy{scale}"
                    seg.append(f"{{\\c{col}{stag}}}{disp[j]}")
                parts.append(" ".join(seg))
            body = "\\N".join(parts)
            anim = ""
            if k == 0:  # entrada suave do grupo: fade + leve subida + escala 94→100
                anim = "\\fad(140,0)\\fscx94\\fscy94\\t(0,220,0.6,\\fscx100\\fscy100)"
                pos = f"\\move({width // 2},{y + 14},{width // 2},{y},0,220)"
            else:
                pos = f"\\pos({width // 2},{y})"
            lines.append(f"Dialogue: 1,{_t(start)},{_t(end)},Cap,,0,0,0,,{{{pos}{anim}}}{body}")
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)
