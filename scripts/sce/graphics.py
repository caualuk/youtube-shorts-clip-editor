"""Elementos gráficos animados (PIL → RGBA numpy) compostos quadro a quadro no renderizador."""
from __future__ import annotations

import math
import textwrap
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .core import font_path

FONT_FILES = {"Inter Black": "Inter-Black.otf", "Inter ExtraBold": "Inter-ExtraBold.otf",
              "Inter SemiBold": "Inter-SemiBold.otf", "Poppins": "Poppins-Bold.ttf",
              "Poppins Medium": "Poppins-Medium.ttf", "Poppins Italic": "Poppins-BoldItalic.ttf"}


@lru_cache(maxsize=64)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(FONT_FILES.get(name, "Poppins-Bold.ttf")), size)


def ease_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def ease_out_back(x: float) -> float:
    x = min(1.0, max(0.0, x))
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _shadowed(img: Image.Image, radius: int = 18, alpha: int = 140) -> Image.Image:
    pad = radius * 2
    big = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, alpha))
    sh.putalpha(img.split()[3].point(lambda a: min(alpha, a)))
    big.paste(sh, (pad, pad + 10), sh)
    big = big.filter(ImageFilter.GaussianBlur(radius))
    big.alpha_composite(img, (pad, pad))
    return big


def hook_card(text: str, style: dict, width: int = 940) -> np.ndarray:
    """Título editorial do topo (gancho)."""
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    t = text.upper() if style["uppercase"] else text
    for size in (76, 68, 60, 54, 48):  # reduz a fonte até caber em 3 linhas — nunca corta o texto
        fnt = font(style["font"], size)
        lines = _wrap(tmp, t, fnt, width - 90)
        if len(lines) <= 3:
            break
    lh = int(fnt.size * 1.15)
    h = lh * len(lines) + 70
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, width - 1, h - 1], radius=34, fill=(255, 255, 255, 245))
    d.rounded_rectangle([0, h - 14, width - 1, h - 1], radius=7, fill=style["accent"] + (255,))
    y = 30
    for ln in lines:
        tw = d.textlength(ln, font=fnt)
        d.text(((width - tw) / 2, y), ln, font=fnt, fill=(12, 12, 16, 255))
        y += lh
    return np.array(_shadowed(img))


def pill(text: str, style: dict) -> np.ndarray:
    fnt = font("Inter SemiBold", 40)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = int(tmp.textlength(text, font=fnt))
    w, h = tw + 84, 72
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=36, fill=style["card_bg"])
    d.ellipse([22, 26, 42, 46], fill=style["accent"] + (255,))
    d.text((56, 12), text, font=fnt, fill=(255, 255, 255, 255))
    return np.array(img)


def info_card(label: str, text: str, style: dict, source: str | None = None, width: int = 900) -> np.ndarray:
    fl, ft, fs = font("Inter ExtraBold", 34), font("Poppins Medium", 46), font("Poppins Medium", 28)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = _wrap(tmp, text, ft, width - 110)[:5]
    h = 90 + len(lines) * 60 + (54 if source else 20)
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, width - 1, h - 1], radius=28, fill=style["card_bg"])
    d.rounded_rectangle([0, 0, 14, h - 1], radius=7, fill=style["accent"] + (255,))
    d.text((48, 28), label.upper(), font=fl, fill=style["accent"] + (255,))
    y = 80
    for ln in lines:
        d.text((48, y), ln, font=ft, fill=(255, 255, 255, 255))
        y += 60
    if source:
        src = source if len(source) < 60 else source[:57] + "…"
        d.text((48, y + 8), f"Fonte: {src}", font=fs, fill=(200, 200, 210, 230))
    return np.array(_shadowed(img, 14, 120))


def explainer_frame(ex: dict, style: dict, progress: float, width: int = 920) -> np.ndarray:
    """Gráfico explicativo animado. Tipos: list, stat, compare. progress 0..1."""
    kind = ex.get("kind") or ex.get("type_") or ex.get("graphic", "list")
    title = ex.get("title", "")
    ft, fi = font("Inter ExtraBold", 44), font("Poppins Medium", 44)
    if kind == "stat":
        h = 380
    elif kind == "contrast":
        h = 340
    elif kind == "compare":
        h = 140 + 120 * len(ex.get("items", []))
    else:
        h = 140 + 86 * len(ex.get("items", []))
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, width - 1, h - 1], radius=30, fill=style["card_bg"])
    d.text((44, 32), title.upper(), font=ft, fill=style["accent"] + (255,))
    acc = style["accent"] + (255,)
    if kind == "stat":
        val = float(ex.get("value", 0))
        shown = val * ease_out(progress * 1.4)
        txt = (ex.get("prefix", "") + (f"{shown:,.0f}" if val >= 10 else f"{shown:.1f}").replace(",", ".")
               + ex.get("suffix", ""))
        fb = font("Inter Black", 150)
        tw = d.textlength(txt, font=fb)
        d.text(((width - tw) / 2, 100), txt, font=fb, fill=(255, 255, 255, 255))
        cap = ex.get("caption", "")
        if cap:
            fc = font("Poppins Medium", 38)
            cw = d.textlength(cap, font=fc)
            d.text(((width - cw) / 2, 290), cap, font=fc, fill=(220, 220, 230, 255))
    elif kind == "contrast":
        items = (ex.get("items") or ["", ""])[:2]
        fi2 = font("Poppins Medium", 42)
        for i, it in enumerate(items):
            local = ease_out((progress * 1.8) - i * 0.35)
            if local <= 0:
                continue
            y = 118 + i * 104
            a = int(255 * local)
            xoff = int((1 - local) * 50)
            col = (255, 80, 90, a) if i == 0 else (90, 230, 140, a)
            d.rounded_rectangle([44 + xoff, y, 98 + xoff, y + 54], radius=14, fill=col)
            ink = (15, 15, 15, a)
            bx = 44 + xoff
            if i == 0:  # X desenhado (independe de glifo na fonte)
                d.line([bx + 15, y + 14, bx + 39, y + 40], fill=ink, width=7)
                d.line([bx + 39, y + 14, bx + 15, y + 40], fill=ink, width=7)
            else:
                d.line([bx + 13, y + 28, bx + 23, y + 39, bx + 42, y + 15], fill=ink, width=7, joint="curve")
            txt = str(it)
            fi2 = font("Poppins Medium", 42)
            for size in (42, 38, 34, 30):
                fi2 = font("Poppins Medium", size)
                if d.textlength(txt, font=fi2) <= width - 170:
                    break
            while d.textlength(txt, font=fi2) > width - 170 and len(txt) > 4:
                txt = txt[:-2].rstrip() + "…"
            d.text((120 + xoff, y + 2), txt, font=fi2, fill=(255, 255, 255, a if i else int(a * 0.75)))
            if i == 0:  # risco sobre a ideia descartada
                tw = d.textlength(txt, font=fi2)
                d.line([120 + xoff, y + 30, 120 + xoff + tw * local, y + 30], fill=(255, 80, 90, a), width=4)
    elif kind == "compare":
        items = ex.get("items", [])
        vals = [float(v) for v in ex.get("values", [1] * len(items))]
        vmax = max(vals) or 1
        for i, (it, v) in enumerate(zip(items, vals)):
            y = 120 + i * 120
            local = ease_out((progress * 1.6) - i * 0.18)
            d.text((44, y), str(it), font=font("Poppins Medium", 38), fill=(255, 255, 255, 255))
            bw = int((width - 100) * (v / vmax) * local)
            d.rounded_rectangle([44, y + 54, 44 + max(8, bw), y + 96], radius=18, fill=acc)
    else:
        items = ex.get("items", [])
        for i, it in enumerate(items):
            local = ease_out((progress * 1.8) - i * 0.22)
            if local <= 0:
                continue
            y = 126 + i * 86
            xoff = int((1 - local) * 60)
            a = int(255 * local)
            d.ellipse([44 + xoff, y + 12, 80 + xoff, y + 48], fill=style["accent"] + (a,))
            d.text((52 + xoff, y + 10), str(i + 1), font=font("Inter Black", 28), fill=(10, 10, 10, a))
            d.text((104 + xoff, y), str(it)[:34], font=fi, fill=(255, 255, 255, a))
    return np.array(img)


def progress_bar(width: int, frac: float, style: dict, h: int = 10) -> np.ndarray:
    arr = np.zeros((h, width, 4), dtype=np.uint8)
    arr[..., :3] = 255
    arr[..., 3] = 60
    n = int(width * min(1, max(0, frac)))
    arr[:, :n, :3] = style["accent"]
    arr[:, :n, 3] = 235
    return arr


def composite(dst: np.ndarray, src: np.ndarray, x: int, y: int, opacity: float = 1.0) -> None:
    """Alpha blend RGBA src sobre dst RGB in-place, com recorte nas bordas."""
    if opacity <= 0:
        return
    H, W = dst.shape[:2]
    h, w = src.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    s = src[y0 - y:y1 - y, x0 - x:x1 - x]
    a = (s[..., 3:4].astype(np.float32) / 255.0) * opacity
    region = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = (region * (1 - a) + s[..., :3].astype(np.float32) * a).astype(np.uint8)


def scale_rgba(arr: np.ndarray, s: float) -> np.ndarray:
    import cv2
    s = max(0.05, s)
    return cv2.resize(arr, (max(1, int(arr.shape[1] * s)), max(1, int(arr.shape[0] * s))),
                      interpolation=cv2.INTER_LINEAR)


def wobble(t: float, seed: float = 0.0) -> tuple[float, float]:
    """Movimento de câmera de mão simulado (soma de senoides, amplitude sutil)."""
    dx = 0.6 * math.sin(2 * math.pi * t / 4.7 + seed) + 0.4 * math.sin(2 * math.pi * t / 2.3 + seed * 2)
    dy = 0.6 * math.sin(2 * math.pi * t / 5.9 + seed * 3) + 0.4 * math.sin(2 * math.pi * t / 3.1 + seed)
    return dx, dy
