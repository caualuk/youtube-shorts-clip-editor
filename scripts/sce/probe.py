"""Etapa 1 — identificação do arquivo: duração, resolução, FPS, proporção e qualidade do áudio.

A medição de áudio é feita em streaming pelo FFmpeg (astats/ebur128) sobre uma amostra
distribuída ao longo do vídeo, sem carregar o arquivo inteiro em memória.
"""
from __future__ import annotations

import re
from pathlib import Path

from .core import PipelineError, ffprobe_json, log, parse_rate, run

SUPPORTED = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts", ".mts", ".flv", ".wmv"}


def _audio_sample_stats(path: Path, duration: float) -> dict:
    """Mede nível médio, pico e piso de ruído em até 6 janelas de 20s espalhadas pelo vídeo."""
    n = 6 if duration > 180 else 1
    span = min(20.0, duration)
    stats = []
    for i in range(n):
        start = max(0.0, (duration - span) * (i + 0.5) / n) if n > 1 else 0.0
        p = run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.2f}", "-t", f"{span:.2f}", "-i", path,
                 "-vn", "-af", "astats=metadata=0:reset=0:measure_perchannel=none", "-f", "null", "-"],
                desc="astats", check=False)
        txt = p.stderr
        def grab(key):
            m = re.findall(rf"{key}:\s*(-?[\d.]+|-inf)", txt)
            if not m:
                return None
            v = m[-1]
            return -120.0 if v == "-inf" else float(v)
        stats.append({"rms": grab("RMS level dB"), "peak": grab("Peak level dB"),
                      "noise_floor": grab("Noise floor dB")})
    def avg(k):
        vals = [s[k] for s in stats if s[k] is not None]
        return round(sum(vals) / len(vals), 2) if vals else None
    rms, peak, nf = avg("rms"), max([s["peak"] for s in stats if s["peak"] is not None], default=None), avg("noise_floor")
    issues = []
    if rms is not None and rms < -35:
        issues.append("Áudio muito baixo (RMS < -35 dBFS) — será normalizado.")
    if peak is not None and peak > -0.1:
        issues.append("Possível clipping (pico ≥ -0.1 dBFS).")
    if nf is not None and nf > -50:
        issues.append("Piso de ruído alto — redução de ruído será aplicada.")
    return {"rms_db": rms, "peak_db": peak, "noise_floor_db": nf, "issues": issues}


def probe_video(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise PipelineError(f"Arquivo não encontrado: {path}")
    if path.suffix.lower() not in SUPPORTED:
        log.warning("Extensão %s não está na lista testada; tentando mesmo assim via FFmpeg.", path.suffix)
    info = ffprobe_json(path)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if v is None:
        raise PipelineError("O arquivo não contém trilha de vídeo.")
    if a is None:
        raise PipelineError("O arquivo não contém trilha de áudio — transcrição impossível.")
    duration = float(info["format"].get("duration") or v.get("duration") or 0)
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    if abs(rot) in (90, 270):
        w, h = h, w
    fps = parse_rate(v.get("avg_frame_rate")) or parse_rate(v.get("r_frame_rate"))
    res = {
        "path": str(path.resolve()),
        "container": info["format"].get("format_name"),
        "size_bytes": int(info["format"].get("size") or 0),
        "duration": round(duration, 3),
        "width": w, "height": h, "rotation": rot,
        "aspect_ratio": round(w / h, 4) if h else None,
        "orientation": "vertical" if h > w else ("quadrado" if h == w else "horizontal"),
        "fps": round(fps, 3),
        "video_codec": v.get("codec_name"),
        "video_bitrate": int(v.get("bit_rate") or 0) or None,
        "audio_codec": a.get("codec_name"),
        "audio_sample_rate": int(a.get("sample_rate") or 0),
        "audio_channels": int(a.get("channels") or 0),
    }
    res["audio_quality"] = _audio_sample_stats(path, duration)
    warnings = []
    if h < 720 and w < 720:
        warnings.append("Resolução baixa (<720p): o reenquadramento 9:16 vai ampliar a imagem e perder nitidez.")
    if duration < 60:
        warnings.append("Vídeo curto (<60s): dificilmente haverá 10 momentos independentes.")
    res["warnings"] = warnings + res["audio_quality"]["issues"]
    return res
