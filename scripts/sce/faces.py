"""Detecção e rastreamento de rosto para reenquadramento 9:16.

Usa o detector Haar do OpenCV (incluído em assets/models, sem download) em quadros reduzidos,
com suavização exponencial + zona morta para evitar "câmera nervosa". Se o OpenCV tiver o
YuNet (cv2.FaceDetectorYN) e o modelo existir em assets/models/face_detection_yunet.onnx,
ele é usado por ser mais robusto a perfis e iluminação ruim.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .core import ASSETS, log

_HAAR = ASSETS / "models" / "haarcascade_frontalface_default.xml"
_YUNET = ASSETS / "models" / "face_detection_yunet.onnx"


class FaceDetector:
    def __init__(self, work_width: int = 480):
        self.work_width = work_width
        self.yunet = None
        if _YUNET.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.yunet = cv2.FaceDetectorYN.create(str(_YUNET), "", (320, 320), 0.7)
                log.debug("Usando YuNet para rostos.")
            except Exception as e:  # pragma: no cover
                log.warning("YuNet indisponível (%s); usando Haar.", e)
        self.haar = cv2.CascadeClassifier(str(_HAAR))
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        if self.haar.empty() and self.yunet is None:
            raise RuntimeError(f"Modelo Haar não carregou: {_HAAR}")

    def detect(self, frame_rgb: np.ndarray) -> list[tuple[float, float, float, float]]:
        """Retorna rostos como (cx, cy, w, h) normalizados 0..1, maior primeiro."""
        h, w = frame_rgb.shape[:2]
        scale = self.work_width / w
        small = cv2.resize(frame_rgb, (self.work_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        faces = []
        if self.yunet is not None:
            self.yunet.setInputSize((small.shape[1], small.shape[0]))
            _, det = self.yunet.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
            if det is not None:
                for d in det:
                    x, y, fw, fh = d[:4]
                    faces.append((x, y, fw, fh))
        else:
            # CLAHE (contraste local) em vez de equalização global: rostos pequenos numa facecam
            # sobre fundo grande e uniforme somem com equalizeHist
            gray = self.clahe.apply(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY))
            min_side = max(20, int(small.shape[0] * 0.05))
            for (x, y, fw, fh) in self.haar.detectMultiScale(gray, 1.1, 5, minSize=(min_side, min_side)):
                faces.append((x, y, fw, fh))
        sw, sh = small.shape[1], small.shape[0]
        out = [((x + fw / 2) / sw, (y + fh / 2) / sh, fw / sw, fh / sh) for x, y, fw, fh in faces]
        return sorted(out, key=lambda f: -f[2] * f[3])


class SmoothTracker:
    """Suaviza o centro horizontal do enquadramento (zona morta + EMA)."""

    def __init__(self, init_x: float = 0.5, alpha: float = 0.08, deadzone: float = 0.04):
        self.x = init_x
        self.target = init_x
        self.alpha = alpha
        self.deadzone = deadzone
        self.misses = 0

    def update(self, face_x: float | None) -> float:
        if face_x is not None:
            self.misses = 0
            if abs(face_x - self.target) > self.deadzone:
                self.target = face_x
        else:
            self.misses += 1
            if self.misses > 90:  # ~3s sem rosto: volta devagar ao centro
                self.target = 0.5
        self.x += (self.target - self.x) * self.alpha
        return self.x


def sample_face_presence(video: Path, times: list[float], detector: FaceDetector | None = None) -> dict:
    """Amostra quadros em tempos específicos (seek, sem decodificar o vídeo todo)."""
    detector = detector or FaceDetector()
    cap = cv2.VideoCapture(str(video))
    hits, xs, sizes = 0, [], []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        faces = detector.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if faces:
            hits += 1
            xs.append(faces[0][0])
            sizes.append(faces[0][3])
    cap.release()
    n = max(1, len(times))
    return {"face_ratio": round(hits / n, 2),
            "face_x_mean": round(float(np.mean(xs)), 3) if xs else None,
            "face_size_mean": round(float(np.mean(sizes)), 3) if sizes else None}


# ------------------------------------------------------------------ vídeos de reação
CORNERS = {"top-left": (0.0, 0.0), "top-right": (1.0, 0.0), "bottom-left": (0.0, 1.0), "bottom-right": (1.0, 1.0)}


def detect_facecam(video: Path, start: float, end: float, src_w: int, src_h: int, hint: str = "auto",
                   panel_aspect: float = 1.25, samples: int = 14, detector: FaceDetector | None = None) -> dict | None:
    """Localiza a janela da câmera de reação (facecam) num vídeo de reação.

    A facecam é um rosto que fica PARADO no mesmo lugar do quadro durante o trecho; rostos que fazem
    parte do conteúdo (o vídeo/jogo sendo assistido) se movem ou somem. Amostramos quadros, agrupamos
    detecções por posição e escolhemos o grupo mais persistente e estável (respeitando a dica de canto,
    se houver). Retorna a região em pixels da fonte (x, y, w, h) com a proporção do painel de reação.
    """
    detector = detector or FaceDetector(work_width=960)  # facecams são pequenas: mais resolução
    times = list(np.linspace(start + 0.3, max(start + 0.4, end - 0.3), samples))
    cap = cv2.VideoCapture(str(video))
    dets, grays = [], []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if g.shape[1] != src_w:
            g = cv2.resize(g, (src_w, src_h))
        grays.append(g)
        for f in detector.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))[:4]:
            dets.append(f)
    cap.release()
    clusters: list[list] = []
    for d in dets:
        for c in clusters:
            if abs(c[0][0] - d[0]) < 0.06 and abs(c[0][1] - d[1]) < 0.08:
                c.append(d)
                break
        else:
            clusters.append([d])
    best, best_sc = None, 0.0
    for c in clusters:
        arr = np.array(c)
        persistence = len(c) / max(1, samples)
        if persistence < 0.35:
            continue
        spread = float(arr[:, 0].std() + arr[:, 1].std())
        cx, cy = float(arr[:, 0].mean()), float(arr[:, 1].mean())
        sc = persistence - spread * 4
        if hint in CORNERS:
            hx, hy = CORNERS[hint]
            sc += 0.6 - (abs(cx - hx) + abs(cy - hy)) * 0.5
        else:  # facecams costumam ficar fora do centro
            sc += min(abs(cx - 0.5), 0.35)
        if sc > best_sc:
            best, best_sc = (cx, cy, float(np.median(arr[:, 2])), float(np.median(arr[:, 3]))), sc
    if best is None:
        if hint not in CORNERS:
            return None
        hx, hy = CORNERS[hint]  # sem rosto detectado: usa o canto indicado (~30% da largura)
        w = 0.30 * src_w
        h = min(src_h * 0.45, w / panel_aspect)
        x = (src_w - w) if hx else 0
        y = (src_h - h) if hy else 0
        return {"x": int(x), "y": int(y), "w": int(w), "h": int(h), "face": None, "detected": False}
    cx, cy, fw, fh = best
    rect = _overlay_rect(grays, cx * src_w, cy * src_h, fw * src_w, fh * src_h)
    if rect:
        x0, y0, x1, y1 = rect
        return {"x": int(x0), "y": int(y0), "w": int(x1 - x0), "h": int(y1 - y0),
                "face": [round(v, 4) for v in best], "detected": True, "bounds": "bordas"}
    w = min(src_w * 0.55, max(src_w * 0.16, fw * src_w * 3.0))
    h = w / panel_aspect
    if h > src_h * 0.6:
        h = src_h * 0.6
        w = h * panel_aspect
    x = cx * src_w - w / 2
    y = cy * src_h - h * 0.45  # rosto um pouco acima do centro do painel
    x = min(max(0, x), src_w - w)
    y = min(max(0, y), src_h - h)
    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h), "face": [round(v, 4) for v in best],
            "detected": True}


def _overlay_rect(grays: list, fx: float, fy: float, fw: float, fh: float):
    """Encontra a moldura retangular da facecam ao redor do rosto.

    Bordas da janela da câmera são linhas retas que ficam PARADAS; o conteúdo ao redor muda. Somamos o
    gradiente de vários quadros: as bordas fixas se reforçam e o resto se dilui. Procuramos a linha mais
    forte de cada lado do rosto; se a câmera encosta na borda do vídeo, usamos a borda do vídeo.
    """
    if len(grays) < 3:
        return None
    H, W = grays[0].shape
    gx_acc = np.zeros((H, W), np.float32)
    gy_acc = np.zeros((H, W), np.float32)
    for g in grays:
        gf = g.astype(np.float32)
        gx_acc += np.abs(cv2.Sobel(gf, cv2.CV_32F, 1, 0, ksize=3))
        gy_acc += np.abs(cv2.Sobel(gf, cv2.CV_32F, 0, 1, ksize=3))
    # perfis só na faixa do rosto (para não pegar bordas de outras coisas)
    r0, r1 = int(max(0, fy - fh)), int(min(H, fy + fh))
    c0, c1 = int(max(0, fx - fw)), int(min(W, fx + fw))
    col = gx_acc[r0:r1].mean(axis=0)
    row = gy_acc[:, c0:c1].mean(axis=1)

    def peak(profile, lo, hi, edge_value):
        lo, hi = int(max(1, lo)), int(min(len(profile) - 2, hi))
        if hi - lo < 4:
            return edge_value
        seg = profile[lo:hi]
        ref = np.median(profile) + 1e-3
        i = int(np.argmax(seg))
        return lo + i if seg[i] > ref * 4 else edge_value

    left = peak(col, fx - 4.5 * fw, fx - 0.9 * fw, 0)
    right = peak(col, fx + 0.9 * fw, fx + 4.5 * fw, W)
    top = peak(row, fy - 4.0 * fh, fy - 0.9 * fh, 0)
    bottom = peak(row, fy + 0.9 * fh, fy + 4.0 * fh, H)
    w, h = right - left, bottom - top
    if w < 2 * fw or h < 2 * fh or w > 0.7 * W or h > 0.8 * H:
        return None
    pad = 4  # entra um pouco para não pegar a própria moldura
    return (left + pad, top + pad, right - pad, bottom - pad)


class MotionTracker:
    """Acompanha onde está a AÇÃO no conteúdo (diferença entre quadros), com suavização forte.

    Serve para o enquadramento do conteúdo seguir o que importa sem movimentos bruscos:
    zona morta larga + EMA lenta + limite de velocidade.
    """

    def __init__(self, exclude: tuple | None = None, alpha: float = 0.035, deadzone: float = 0.08,
                 max_step: float = 0.006):
        self.prev = None
        self.x = 0.5
        self.y = 0.5
        self.tx, self.ty = 0.5, 0.5
        self.alpha, self.deadzone, self.max_step = alpha, deadzone, max_step
        self.exclude = exclude  # região (x0,y0,x1,y1) normalizada a ignorar (ex.: facecam)
        self.activity = 0.0

    def update(self, frame_rgb: np.ndarray, analyze: bool = True) -> tuple[float, float]:
        if analyze:
            small = cv2.cvtColor(cv2.resize(frame_rgb, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
            small = small.astype(np.float32)
            if self.prev is not None:
                diff = np.abs(small - self.prev)
                diff[diff < 12] = 0
                if self.exclude:
                    x0, y0, x1, y1 = self.exclude
                    diff[int(y0 * 90):int(y1 * 90) + 1, int(x0 * 160):int(x1 * 160) + 1] = 0
                tot = float(diff.sum())
                self.activity = 0.9 * self.activity + 0.1 * min(1.0, tot / (160 * 90 * 6))
                if tot > 160 * 90 * 0.5:
                    ys, xs = np.mgrid[0:90, 0:160]
                    mx = float((diff * xs).sum() / tot) / 160
                    my = float((diff * ys).sum() / tot) / 90
                    if abs(mx - self.tx) > self.deadzone:
                        self.tx = mx
                    if abs(my - self.ty) > self.deadzone:
                        self.ty = my
            self.prev = small
        for attr, tgt in (("x", self.tx), ("y", self.ty)):
            cur = getattr(self, attr)
            step = (tgt - cur) * self.alpha
            step = max(-self.max_step, min(self.max_step, step))
            setattr(self, attr, cur + step)
        return self.x, self.y
