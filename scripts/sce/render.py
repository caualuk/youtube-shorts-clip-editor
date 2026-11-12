"""Etapa 9/10 — renderização com câmera e transições FLUIDAS.

Arquitetura: FFmpeg decodifica só a janela do corte → numpy/OpenCV compõem cada quadro → FFmpeg recebe
os quadros por pipe, queima as legendas ASS (libass) e codifica H.264/AAC 1080x1920.

Princípios de câmera (prioridade: o espectador precisa entender o que está acontecendo):
* layout "content"  — sem pessoa em destaque: o CONTEÚDO é prioridade. Mostra ~75% da largura do quadro
  (essência da cena), seguindo a ação com movimento lento; fundo desfocado preenche o resto.
* layout "face"     — pessoa falando (podcast, vlog): rosto acompanhado com zona morta e suavização.
* layout "reaction" — vídeo de reação: tela dividida conteúdo (cima) + reação (baixo). Um "diretor"
  planeja ANTES de renderizar quanto espaço cada um recebe: mais reação nos picos de fala/ênfase, mais
  conteúdo quando quem reage está em silêncio; tudo filtrado para mudar devagar (sem saltos).
* Nenhum texto/cartão é posicionado sobre o rosto de quem reage.

Transições: abertura com fade, dissolve curto em cada jump cut, zooms com easing longo, cartões com
fade+deslize. Sem efeitos sonoros de transição.
"""
from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

from . import graphics as gx
from .audio import build_audio
from .captions import build_ass
from .core import FONTS_DIR, PipelineError, log
from .faces import FaceDetector, MotionTracker, SmoothTracker

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


class FrameReader:
    def __init__(self, path: Path, start: float, dur: float, fps: float, height: int | None = None,
                 size: tuple[int, int] | None = None, src_wh: tuple[int, int] | None = None):
        if size:
            w, h = size
            vf = f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
        else:
            sw, sh = src_wh
            h = min(height, sh)
            h -= h % 2
            w = int(round(sw * h / sh / 2)) * 2
            vf = f"fps={fps},scale={w}:{h}"
        self.w, self.h = w, h
        self.scale = h / src_wh[1] if src_wh else 1.0
        self.p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.4f}", "-t",
                                   f"{dur:.4f}", "-i", str(path), "-an", "-vf", vf, "-pix_fmt", "rgb24",
                                   "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.nbytes = w * h * 3
        self.last = None

    def read(self) -> np.ndarray | None:
        buf = self.p.stdout.read(self.nbytes)
        if len(buf) < self.nbytes:
            return None
        self.last = np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 3)
        return self.last

    def close(self):
        try:
            self.p.stdout.close()
            self.p.kill()
        except Exception:
            pass


def smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _env_punch(dt: float) -> float:
    """Ênfase lenta: entra em 0,5 s, segura 0,8 s, sai em 0,7 s (sempre com easing)."""
    if dt < 0:
        return 0.0
    if dt < 0.5:
        return smoothstep(dt / 0.5)
    if dt < 1.3:
        return 1.0
    if dt < 2.0:
        return 1 - smoothstep((dt - 1.3) / 0.7)
    return 0.0


def _zoom_at(t: float, cam: dict) -> float:
    z = 1 + cam["push_in"] * smoothstep(t / max(1e-3, cam["duration"]))
    for p in cam["punches"]:
        z += p["amount"] * _env_punch(t - p["t"])
    if t < 1.0:  # abertura: assenta suavemente de 1.06 para 1.0
        z *= 1 + 0.06 * (1 - smoothstep(t / 1.0))
    return z


def _cover(img: np.ndarray, W: int, H: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = max(W / w, H / h)
    nw, nh = int(math.ceil(w * s)), int(math.ceil(h * s))
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    x, y = (nw - W) // 2, (nh - H) // 2
    return r[y:y + H, x:x + W]


def _crop(frame, cx, cy, cw, ch):
    h, w = frame.shape[:2]
    cw, ch = max(8, min(w, cw)), max(8, min(h, ch))
    x0 = int(round(min(max(0, cx - cw / 2), w - cw)))
    y0 = int(round(min(max(0, cy - ch / 2), h - ch)))
    return frame[y0:y0 + int(ch), x0:x0 + int(cw)]


def _blur_bg(img: np.ndarray, W: int, H: int, dim: float = 0.45) -> np.ndarray:
    small = _cover(img, max(8, W // 10), max(8, H // 10))
    bg = cv2.GaussianBlur(small, (0, 0), 3)
    return (cv2.resize(bg, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32) * dim).astype(np.uint8)


def render_panel(src: np.ndarray, cx: float, cy: float, crop_w: float, crop_h: float, W: int, H: int) -> np.ndarray:
    """Recorta (crop_w x crop_h em px da fonte, centro cx/cy) e encaixa num painel W x H.
    Se a proporção do recorte for mais larga que o painel, o recorte ocupa a largura toda e o resto
    vira fundo desfocado do próprio conteúdo (nunca barras pretas)."""
    piece = _crop(src, cx, cy, crop_w, crop_h)
    ph, pw = piece.shape[:2]
    if abs(pw / ph - W / H) < 0.02:
        return cv2.resize(piece, (W, H), interpolation=cv2.INTER_LINEAR)
    out = _blur_bg(piece, W, H)
    if pw / ph > W / H:
        fh = int(round(W * ph / pw))
        fg = cv2.resize(piece, (W, fh), interpolation=cv2.INTER_LINEAR)
        y0 = (H - fh) // 2
        out[y0:y0 + fh] = fg
    else:
        fw = int(round(H * pw / ph))
        fg = cv2.resize(piece, (fw, H), interpolation=cv2.INTER_LINEAR)
        x0 = (W - fw) // 2
        out[:, x0:x0 + fw] = fg
    return out


def _schedule(elements: list[dict], duration: float) -> list[dict]:
    """Evita sobreposição temporal de cartões na mesma região."""
    out, busy_until = [], 3.3
    for e in sorted([e for e in elements if e["type"] in ("context_card", "fact", "explainer")],
                    key=lambda e: e["at"]):
        at = max(e["at"], busy_until + 0.4)
        d = min(e["duration"], duration - 2.8 - at)
        if d < 1.8:
            log.warning("Elemento editorial '%s' não coube na timeline (%.1fs) e foi omitido.", e["type"], duration)
            e["_omitted"] = True
            continue
        out.append(dict(e, at=round(at, 2), duration=round(d, 2)))
        busy_until = at + d
    return out


# ---------------------------------------------------------------- diretor (vídeos de reação)
def plan_split(plan: dict, fps: float, n_frames: int, rc: dict) -> np.ndarray:
    """Fração da altura dada ao CONTEÚDO em cada quadro (o resto é a reação).

    Base equilibrada; durante picos de ênfase de quem reage o painel de reação cresce; em silêncios
    longos o conteúdo cresce. Curva suavizada (gaussiana ~0,45 s) + limite de variação por quadro.
    """
    base, lo, hi = rc["split_content"], rc["split_min"], rc["split_max"]
    t = np.arange(n_frames) / fps
    target = np.full(n_frames, base, dtype=np.float32)
    last = -10.0
    for p in plan["camera"]["punches"]:  # ênfases na fala de quem reage
        if p["t"] - last < 4.0:
            continue
        last = p["t"]
        m = (t >= p["t"] - 0.2) & (t <= p["t"] + 1.8)
        target[m] = lo
    words = [w for g in plan["captions"] for w in g["words"]]
    for a, b in zip(words, words[1:]):  # silêncio de quem reage → mais conteúdo
        if b["start"] - a["end"] > 1.0:
            m = (t >= a["end"] + 0.2) & (t <= b["start"] - 0.2)
            target[m] = hi
    sigma = max(1, int(0.45 * fps))
    k = np.exp(-0.5 * (np.arange(-3 * sigma, 3 * sigma + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(target, (3 * sigma, 3 * sigma), mode="edge")
    smooth = np.convolve(pad, k, mode="valid")[:n_frames]
    out = np.empty_like(smooth)
    cur = base
    max_step = 0.12 / fps  # no máximo 12% da tela por segundo
    for i, v in enumerate(smooth):
        cur += max(-max_step, min(max_step, v - cur))
        out[i] = cur
    return out


class Compositor:
    def __init__(self, plan: dict, W: int, H: int, broll_base: Path, cfg: dict, decode_scale: float):
        self.p, self.W, self.H = plan, W, H
        self.cfg = cfg
        self.st = plan["style_def"]
        self.dur = plan["duration"]
        self.cam = plan["camera"]
        self.layout = plan["layout"]
        self.tracker = SmoothTracker(plan["face"].get("face_x_mean") or 0.5, alpha=0.05, deadzone=0.06)
        self.face_y = 0.42
        self.decode_scale = decode_scale
        fc = plan.get("facecam")
        self.facecam = None
        exclude = None
        if self.layout == "reaction" and fc:
            s = decode_scale
            self.facecam = {k: fc[k] * s for k in ("x", "y", "w", "h")}
            sw0, sh0 = plan["_src_wh"]
            if fc.get("face"):
                self.facecam["fx"] = fc["face"][0] * sw0 * s
                self.facecam["fy"] = fc["face"][1] * sh0 * s
            sw, sh = plan["_src_wh"]
            exclude = (fc["x"] / sw, fc["y"] / sh, (fc["x"] + fc["w"]) / sw, (fc["y"] + fc["h"]) / sh)
        self.motion = MotionTracker(exclude=exclude)
        self.split = None
        ed = plan["editorial"] + plan["derived"]
        hook = next((e for e in ed if e["type"] == "hook_title"), None)
        self.hook_img = gx.hook_card(hook["text"], self.st) if hook else None
        self.hook_end = 3.4
        self.pill_img = gx.pill(plan["topic_label"][:28], self.st)
        self.cards = []
        for e in _schedule(plan["editorial"], self.dur):
            if e["type"] == "context_card":
                e["_img"] = gx.info_card(e.get("label", "Contexto"), e["text"], self.st)
            elif e["type"] == "fact":
                e["_img"] = gx.info_card("Dado verificável", e["text"], self.st, e.get("source"))
            self.cards.append(e)
        self.closing = next((dict(e) for e in plan["editorial"] if e["type"] == "closing"), None)
        if self.closing:
            self.closing["_img"] = gx.hook_card(self.closing["text"], self.st, 900)
        # nos layouts com painel de reação, cartões ficam menores para preservar o conteúdo
        self.card_scale = 0.86 if self.layout == "reaction" else 1.0
        self.broll = []
        for b in plan.get("broll", []):
            path = Path(b["file"])
            if not path.is_absolute():
                path = broll_base / path
            if not path.exists():
                log.warning("B-roll não encontrado: %s", path)
                continue
            self.broll.append(dict(b, _path=path, _reader=None, _img=None))
        self.seed = plan["index"] * 1.37
        self.content_cov = cfg["camera"]["content_coverage"]

    # ---------------------------------------------------------- camada base
    def base(self, src: np.ndarray, t: float, fi: int, face, analyze_motion: bool) -> np.ndarray:
        W, H = self.W, self.H
        h, w = src.shape[:2]
        z = _zoom_at(t, self.cam)
        dx, dy = gx.wobble(t, self.seed)
        amp = 0.0025
        if self.layout == "face":
            if face is not None:
                self.face_y = 0.94 * self.face_y + 0.06 * face[1]
            fx = self.tracker.update(face[0] if face is not None else None)
            ch = h / z
            cw = ch * W / H
            if cw > w:
                cw = w / z
                ch = cw * H / W
            cy = self.face_y * h + ch * 0.06 + dy * amp * h  # rosto perto de 45% da altura
            return np.ascontiguousarray(render_panel(src, fx * w + dx * amp * w, cy, cw, ch, W, H))
        if self.layout == "fill":  # fonte já vertical: preenche a tela com zoom suave
            return np.ascontiguousarray(render_panel(src, w / 2 + dx * amp * w, h / 2 + dy * amp * h,
                                                     w / z, h / z, W, H))
        mx, my = self.motion.update(src, analyze_motion)
        if self.layout == "reaction" and self.facecam is not None:
            frac = float(self.split[min(fi, len(self.split) - 1)])
            top_h = int(round(H * frac / 2) * 2)
            bot_h = H - top_h
            # conteúdo: segue a ação, mostra a essência (cobertura configurável)
            cw = self.content_cov * w / z
            ch = min(h, cw * top_h / W)
            tgt = self._avoid_cam_x(mx * w, cw, w)
            self._cx = tgt if getattr(self, "_cx", None) is None else self._cx + (tgt - self._cx) * 0.04
            top = render_panel(self._hide_cam(src), self._cx + dx * amp * w, my * h, cw, ch, W, top_h)
            # reação: enquadra o rosto dentro da facecam, com zoom de ênfase suave
            fc = self.facecam
            zr = 1 + (z - 1) * 1.6
            # recorte sempre DENTRO da janela da câmera (nada do jogo vaza para o painel de reação)
            rcw = fc["w"] / zr
            rch = rcw * bot_h / W
            if rch > fc["h"]:
                rch = fc["h"] / zr
                rcw = rch * W / bot_h
            fx_, fy_ = (fc["fx"], fc["fy"]) if fc.get("fx") is not None else (fc["x"] + fc["w"] / 2, fc["y"] + fc["h"] / 2)
            rcx = min(max(fx_, fc["x"] + rcw / 2), fc["x"] + fc["w"] - rcw / 2)
            rcy = min(max(fy_ + rch * 0.08, fc["y"] + rch / 2), fc["y"] + fc["h"] - rch / 2)
            cam = src[int(fc["y"]):int(fc["y"] + fc["h"]), int(fc["x"]):int(fc["x"] + fc["w"])]
            bottom = render_panel(cam, rcx - fc["x"], rcy - fc["y"], rcw, rch, W, bot_h)
            out = np.empty((H, W, 3), dtype=np.uint8)
            out[:top_h] = top
            out[top_h:] = bottom
            out[top_h - 3:top_h + 3] = self.st["accent"]  # divisória discreta na cor do estilo
            self._seam = top_h
            return out
        # layout "content" (prioridade ao conteúdo)
        cw = self.content_cov * w / z
        ch = min(h, cw * 0.82)  # recorte ~16:13: maior que o quadro inteiro, sem perder a cena
        cx = mx * w + dx * amp * w
        cy = my * h + dy * amp * h
        out = _blur_bg(src, W, H)
        piece = _crop(src, cx, cy, cw, ch)
        fh = int(round(W * piece.shape[0] / piece.shape[1]))
        fg = cv2.resize(piece, (W, fh), interpolation=cv2.INTER_LINEAR)
        y0 = int(H * 0.47 - fh / 2)
        out[max(0, y0):y0 + fh] = fg[max(0, -y0):]
        return np.ascontiguousarray(out)

    def _avoid_cam_x(self, cx: float, cw: float, w: int) -> float:
        """Desloca o recorte do conteúdo para longe da facecam quando isso não sacrifica a ação."""
        fc = self.facecam
        x0, x1 = fc["x"], fc["x"] + fc["w"]
        if cx + cw / 2 <= x0 or cx - cw / 2 >= x1:
            return cx
        if (x0 + x1) / 2 > w / 2:  # câmera à direita → empurra o recorte para a esquerda
            alt = x0 - cw / 2
        else:
            alt = x1 + cw / 2
        return alt if abs(alt - cx) < cw * 0.35 else cx

    def _hide_cam(self, src: np.ndarray) -> np.ndarray:
        """Remove a facecam do painel de conteúdo (ela já aparece embaixo): reconstrói a área com o
        entorno (inpainting em 1/4 da resolução, rápido) e mistura com borda suave."""
        fc = self.facecam
        H0, W0 = src.shape[:2]
        m = 18  # cobre também a moldura da câmera
        x0, y0 = int(max(0, fc["x"] - m)), int(max(0, fc["y"] - m))
        x1, y1 = int(min(W0, fc["x"] + fc["w"] + m)), int(min(H0, fc["y"] + fc["h"] + m))
        ex = 40  # contexto ao redor usado pelo inpainting
        X0, Y0, X1, Y1 = max(0, x0 - ex), max(0, y0 - ex), min(W0, x1 + ex), min(H0, y1 + ex)
        patch = src[Y0:Y1, X0:X1]
        sw, sh = max(4, patch.shape[1] // 4), max(4, patch.shape[0] // 4)
        small = cv2.resize(patch, (sw, sh), interpolation=cv2.INTER_AREA)
        mask = np.zeros((sh, sw), np.uint8)
        mask[(y0 - Y0) // 4:(y1 - Y0) // 4 + 1, (x0 - X0) // 4:(x1 - X0) // 4 + 1] = 255
        filled = cv2.inpaint(small, mask, 5, cv2.INPAINT_TELEA)
        filled = cv2.GaussianBlur(filled, (0, 0), 2)
        big = cv2.resize(filled, (patch.shape[1], patch.shape[0]), interpolation=cv2.INTER_LINEAR)
        alpha = np.zeros(patch.shape[:2], np.float32)
        alpha[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = 1.0
        alpha = cv2.GaussianBlur(alpha, (0, 0), 4)[..., None]
        out = src.copy()
        out[Y0:Y1, X0:X1] = (patch * (1 - alpha) + big * alpha).astype(np.uint8)
        return out

    # ---------------------------------------------------------- overlays
    def _card_y(self) -> int:
        return int(self.H * (0.075 if self.layout == "reaction" else 0.105))

    def overlays(self, img: np.ndarray, t: float) -> np.ndarray:
        W, H = self.W, self.H
        cs = self.card_scale
        # B-roll (com dissolve de 0,35 s)
        for b in self.broll:
            a0, d = float(b["at"]), float(b.get("duration", 2.0))
            if a0 <= t < a0 + d:
                op = smoothstep(min((t - a0) / 0.35, (a0 + d - t) / 0.35))
                if b["_path"].suffix.lower() in IMAGE_EXT:
                    if b["_img"] is None:
                        b["_img"] = cv2.cvtColor(cv2.imread(str(b["_path"])), cv2.COLOR_BGR2RGB)
                    kb = 1.0 + 0.06 * (t - a0) / d
                    frame = _cover(b["_img"], int(W * kb), int(H * kb))
                    frame = frame[(frame.shape[0] - H) // 2:(frame.shape[0] - H) // 2 + H,
                                  (frame.shape[1] - W) // 2:(frame.shape[1] - W) // 2 + W]
                else:
                    if b["_reader"] is None:
                        b["_reader"] = FrameReader(b["_path"], float(b.get("src_start", 0)), d + 0.5,
                                                   self.p["fps"], size=(W, H))
                    frame = b["_reader"].read()
                    frame = frame if frame is not None else b["_reader"].last
                if frame is not None:
                    if b.get("mode") == "pip":
                        pip = cv2.resize(frame, (W - 140, int((W - 140) * 9 / 16)))
                        rgba = np.dstack([pip, np.full(pip.shape[:2], 255, np.uint8)])
                        gx.composite(img, rgba, 70, int(H * 0.11), op)
                    else:
                        rgba = np.dstack([frame, np.full(frame.shape[:2], 255, np.uint8)])
                        gx.composite(img, rgba, 0, 0, op)
        # título-gancho: fade + leve subida/escala (0,5 s), saída em fade (0,45 s)
        if self.hook_img is not None and t < self.hook_end:
            p_in = smoothstep((t - 0.25) / 0.5)
            p_out = smoothstep((self.hook_end - t) / 0.45)
            op = p_in * p_out
            if op > 0:
                sc = gx.scale_rgba(self.hook_img, cs * (0.96 + 0.04 * p_in))
                y = self._card_y() - 12 + int(18 * (1 - p_in))
                gx.composite(img, sc, (W - sc.shape[1]) // 2, y, op)
        # selo do assunto
        if t >= self.hook_end - 0.3:
            op = smoothstep((t - self.hook_end + 0.3) / 0.5)
            gx.composite(img, self.pill_img, 56, int(H * 0.035), op * 0.95)
        # cartões e gráficos
        side = -1 if self.p["variant"]["card_side"] == "left" else 1
        for e in self.cards:
            a0, d = e["at"], e["duration"]
            if a0 <= t < a0 + d:
                pin = smoothstep((t - a0) / 0.45)
                pout = smoothstep((a0 + d - t) / 0.4)
                im = gx.explainer_frame(e, self.st, (t - a0) / max(0.1, d * 0.6)) if e["type"] == "explainer" else e["_img"]
                if cs != 1.0:
                    im = gx.scale_rgba(im, cs)
                x = (W - im.shape[1]) // 2 + int(side * 60 * (1 - pin))
                gx.composite(img, im, x, self._card_y(), pin * pout)
        # fechamento
        if self.closing and t >= self.closing["at"]:
            ci = self.closing["_img"]
            p_in = smoothstep((t - self.closing["at"]) / 0.5)
            sc = gx.scale_rgba(ci, cs * (0.96 + 0.04 * p_in))
            gx.composite(img, sc, (W - sc.shape[1]) // 2, self._card_y() + int(14 * (1 - p_in)), p_in)
        # barra de progresso
        if self.p["variant"].get("progress_bar", True):
            gx.composite(img, gx.progress_bar(W, t / self.dur, self.st, 8), 0, 0, 1)
        return img

    def close(self):
        for b in self.broll:
            if b["_reader"]:
                b["_reader"].close()


def render_clip(src: Path, plan: dict, out_mp4: Path, tmp: Path, cfg: dict, meta: dict,
                gain_trim_db: float = 0.0) -> dict:
    rc = cfg["render"]
    tc = cfg["transitions"]
    W, H = rc["width"], rc["height"]
    fps = plan["fps"]
    tmp.mkdir(parents=True, exist_ok=True)
    iv = plan["intervals"]
    q = 1.0 / fps
    n_out = int(round(plan["duration"] * fps))
    w0 = max(0.0, math.floor((iv[0][0] - 0.5) / q) * q)
    reader = FrameReader(src, w0, iv[-1][1] - w0 + 0.2, fps, height=rc["decode_height"],
                         src_wh=(meta["width"], meta["height"]))
    plan["_src_wh"] = (meta["width"], meta["height"])
    comp = Compositor(plan, W, H, Path(cfg.get("_project", ".")), cfg, reader.scale)
    if plan["layout"] == "reaction":
        comp.split = plan_split(plan, fps, n_out, cfg["camera"])
    plan["_sfx_cues"] = []  # sem efeitos sonoros de transição
    plan["_scheduled_cards"] = [{k: v for k, v in e.items() if not k.startswith("_")} for e in comp.cards]
    # legendas: posição segura por layout (nunca sobre o rosto de quem reage)
    cap_y = {"reaction": int(H * min(0.47, cfg["camera"]["split_min"] - 0.035)),
             "face": int(H * 0.72), "content": int(H * 0.755)}.get(plan["layout"], int(H * 0.72))
    ass = tmp / "captions.ass"
    n_events = build_ass(plan, ass, W, H, cap_y)
    fonts_local = tmp / "fonts"
    if not fonts_local.exists():
        shutil.copytree(FONTS_DIR, fonts_local)
    audio = build_audio(src, plan, tmp, cfg, meta.get("audio_quality", {}).get("noise_floor_db"), gain_trim_db)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-", "-i", str(audio),
         "-vf", "ass=captions.ass:fontsdir=fonts", "-c:v", "libx264", "-preset", rc["preset"], "-crf", str(rc["crf"]),
         "-profile:v", "high", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
         "-color_trc", "bt709", "-c:a", "aac", "-b:a", rc["audio_bitrate"], "-ar", str(rc["audio_sample_rate"]),
         "-movflags", "+faststart", "-shortest", str(out_mp4.resolve())],
        stdin=subprocess.PIPE, cwd=str(tmp), stderr=subprocess.PIPE)
    det = FaceDetector() if plan["layout"] == "face" else None
    every = rc["face_detect_every_n_frames"]
    dissolve_n = max(1, int(round(tc["cut_dissolve"] * fps)))
    fade_in_n = max(1, int(round(tc["intro_fade"] * fps)))
    face, k, written, last_out = None, 0, 0, None
    cur_seg, blend_from, blend_left = None, None, 0
    try:
        while written < n_out:
            fr = reader.read()
            if fr is None:
                if last_out is None:
                    raise PipelineError("Nenhum quadro decodificado da janela do corte.")
                enc.stdin.write(last_out.tobytes())
                written += 1
                continue
            ts = w0 + k * q
            k += 1
            seg = next((i for i, (a, b) in enumerate(iv) if a - q / 2 <= ts < b - q / 2), None)
            if seg is None:
                continue
            if cur_seg is not None and seg != cur_seg and last_out is not None:
                blend_from, blend_left = last_out.copy(), dissolve_n  # dissolve no jump cut
            cur_seg = seg
            t_out = written * q
            if det is not None and written % every == 0:
                fs = det.detect(fr)
                face = (fs[0][0], fs[0][1]) if fs else None
            img = comp.base(fr, t_out, written, face, analyze_motion=(written % 2 == 0))
            img = comp.overlays(img, t_out)
            if blend_left > 0 and blend_from is not None:
                a = smoothstep(1 - blend_left / (dissolve_n + 1))
                img = cv2.addWeighted(blend_from, 1 - a, img, a, 0)
                blend_left -= 1
            if written < fade_in_n:  # abertura vinda do preto
                img = (img.astype(np.float32) * smoothstep((written + 1) / fade_in_n)).astype(np.uint8)
            enc.stdin.write(np.ascontiguousarray(img).tobytes())
            last_out = img
            written += 1
    finally:
        reader.close()
        comp.close()
        if enc.stdin:
            enc.stdin.close()
        err = enc.stderr.read().decode(errors="ignore") if enc.stderr else ""
        enc.wait()
    if enc.returncode != 0:
        raise PipelineError(f"Falha na codificação final: {err[-2000:]}")
    if comp.split is not None:
        plan["_split_stats"] = {"min": round(float(comp.split.min()), 3), "max": round(float(comp.split.max()), 3),
                                "mean": round(float(comp.split.mean()), 3)}
    return {"frames": written, "caption_events": n_events, "audio": plan.get("_audio_report", {})}
