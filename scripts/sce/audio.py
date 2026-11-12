"""Edição de áudio: corte por intervalos, limpeza da voz, trilha, efeitos e loudness.

Cadeia da voz (FFmpeg): highpass → (afftdn se ruído alto) → EQ (corte de "lama" em 250 Hz,
presença em 3,5 kHz) → compressor moderado (3:1) → mix com trilha "duckada" por sidechain →
loudnorm EBU R128 em duas passagens (alvo -14 LUFS, TP -1.5 dBTP).

Licenciamento: por padrão a trilha e os efeitos são SINTETIZADOS aqui (numpy) — obra gerada pelo
próprio processo, sem terceiros, portanto sem risco de Content ID. Para usar uma biblioteca
própria, configure audio.music_source="library" e audio.music_library_dir com um licenses.json
(o validador recusa faixas sem licença registrada).
"""
from __future__ import annotations

import json
import re
import wave
from pathlib import Path

import numpy as np

from .core import PipelineError, log, read_json, run

SR = 48000

_PROG = {  # progressões (graus em semitons relativos à tônica) por clima
    "energetico": ([0, 7, 9, 5], 0, 124, True),
    "educativo": ([0, 5, 9, 7], 2, 92, False),
    "historia": ([9, 5, 0, 7], -3, 84, False),
    "debate": ([9, 7, 5, 4], -5, 100, True),
    "humor": ([0, 9, 5, 7], 5, 116, True),
}


def _write_wav(path: Path, data: np.ndarray, sr: int = SR) -> None:
    data = np.clip(data, -1, 1)
    if data.ndim == 1:
        data = np.stack([data, data], axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((data * 32767).astype(np.int16).tobytes())


def synth_music(duration: float, mood: str, seed: int = 0) -> np.ndarray:
    """Trilha original procedural: pad de acordes + baixo + (opcional) pulso rítmico."""
    degrees, key, bpm, pulse = _PROG.get(mood, _PROG["educativo"])
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    t = np.arange(n) / SR
    beat = 60.0 / bpm
    bar = beat * 4
    out = np.zeros(n, dtype=np.float32)
    base = 220.0 * 2 ** ((key - 9) / 12)  # A3 relativo
    for bi in range(int(duration / bar) + 2):
        root = degrees[bi % len(degrees)]
        minor = root in (9, 4, 2)
        chord = [0, 3 if minor else 4, 7, 12]
        s, e = int(bi * bar * SR), min(n, int((bi + 1) * bar * SR + 0.3 * SR))
        if s >= n:
            break
        tt = t[s:e] - bi * bar
        env = np.minimum(1, tt / 0.6) * np.exp(-tt / (bar * 1.6))
        for c in chord:
            f = base * 2 ** ((root + c) / 12)
            det = 1 + rng.uniform(-0.002, 0.002)
            out[s:e] += 0.06 * env * (np.sin(2 * np.pi * f * det * tt) + 0.3 * np.sin(2 * np.pi * 2 * f * tt))
        fb = base * 2 ** ((root - 12) / 12)
        out[s:e] += 0.10 * env * np.sin(2 * np.pi * fb * tt)
    if pulse:
        for k in range(int(duration / beat) + 1):
            s = int(k * beat * SR)
            if s >= n:
                break
            L = min(int(0.18 * SR), n - s)
            tt = np.arange(L) / SR
            kick = np.sin(2 * np.pi * (55 + 90 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 18)
            out[s:s + L] += 0.22 * kick
            if k % 2 == 1:
                hh = rng.normal(0, 1, min(int(0.04 * SR), n - s)) * np.exp(-np.arange(min(int(0.04 * SR), n - s)) / SR * 90)
                out[s:s + len(hh)] += 0.02 * hh
    fade = int(0.8 * SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    peak = np.max(np.abs(out)) or 1
    return (out / peak * 0.7).astype(np.float32)


def synth_sfx(duration: float, cues: list[tuple[float, str]]) -> np.ndarray:
    n = int(duration * SR)
    out = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(7)
    for t0, kind in cues:
        s = int(t0 * SR)
        if s >= n:
            continue
        if kind == "whoosh":
            L = int(0.45 * SR)
            tt = np.arange(L) / SR
            noise = rng.normal(0, 1, L)
            env = np.sin(np.pi * tt / tt[-1]) ** 2
            sweep = np.convolve(noise, np.ones(6) / 6, mode="same")
            sig = sweep * env * 0.5
        elif kind == "pop":
            L = int(0.12 * SR)
            tt = np.arange(L) / SR
            sig = np.sin(2 * np.pi * (900 - 2500 * tt) * tt) * np.exp(-tt * 40) * 0.6
        else:  # "tick"
            L = int(0.05 * SR)
            tt = np.arange(L) / SR
            sig = np.sin(2 * np.pi * 2200 * tt) * np.exp(-tt * 90) * 0.4
        e = min(n, s + L)
        out[s:e] += sig[: e - s]
    return out


def _measure_loudnorm(path: Path, cfg: dict) -> dict:
    ac = cfg["audio"]
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
             f"loudnorm=I={ac['target_lufs']}:TP={ac['true_peak']}:LRA={ac['lra']}:print_format=json",
             "-f", "null", "-"], desc="loudnorm medição", check=False)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
    if not m:
        raise PipelineError("Falha ao medir loudness.")
    return json.loads(m.group(0))


def build_audio(src: Path, plan: dict, tmp: Path, cfg: dict, noise_floor_db: float | None,
                gain_trim_db: float = 0.0) -> Path:
    ac = cfg["audio"]
    iv = plan["intervals"]
    dur = plan["duration"]
    # 1) voz cortada nos intervalos, com micro-fades para não estalar nos jump cuts
    voice = tmp / "voice_cut.wav"
    w0 = max(0, iv[0][0] - 1)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{w0:.3f}", "-t",
         f"{iv[-1][1] - w0 + 0.5:.3f}", "-i", src, "-filter_complex", _shift_fc(iv, w0, cfg["transitions"]["audio_cut_fade"]),
         "-map", "[v]", "-ac", "2", "-ar", str(SR), voice], desc="corte de áudio")
    # 2) censura opcional (bipe) de palavras listadas
    censor = plan.get("_censor_times", [])
    # 3) cadeia da voz
    chain = [f"highpass=f={ac['highpass_hz']}"]
    if noise_floor_db is not None and noise_floor_db > ac["denoise_if_noise_floor_above_db"]:
        chain.append("afftdn=nf=-25:tn=1")
    chain += ["equalizer=f=250:t=q:w=1.2:g=-2", "equalizer=f=3500:t=q:w=1.0:g=2.5",
              "acompressor=threshold=-20dB:ratio=3:attack=8:release=160:makeup=2"]
    for (a, b) in censor:
        chain.append(f"volume=enable='between(t,{a:.3f},{b:.3f})':volume=0")
    voice_p = tmp / "voice_proc.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", voice, "-af", ",".join(chain), voice_p],
        desc="tratamento da voz")
    # 4) trilha + efeitos
    inputs, fc_parts = ["-i", str(voice_p)], []
    mix_in = ["[vo]"]
    idx = 1
    fc_parts.append("[0:a]asplit=2[vo][sc]")
    music_file = None
    if plan["music"]["enabled"]:
        if plan["music"]["source"] == "library" and plan["music"].get("file"):
            music_file = Path(plan["music"]["file"])
        else:
            music_file = tmp / "music.wav"
            _write_wav(music_file, synth_music(dur + 0.5, plan["music"]["mood"], seed=plan["index"]))
        inputs += ["-stream_loop", "-1", "-i", str(music_file)]
        fc_parts.append(f"[{idx}:a]atrim=0:{dur:.3f},volume={ac['music_level_db']}dB[mu];"
                        "[mu][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=450[mud]")
        mix_in.append("[mud]")
        idx += 1
    else:
        fc_parts.append("[sc]anullsink")
    if plan["sfx"] and plan.get("_sfx_cues"):
        sfx = tmp / "sfx.wav"
        _write_wav(sfx, synth_sfx(dur, plan["_sfx_cues"]))
        inputs += ["-i", str(sfx)]
        fc_parts.append(f"[{idx}:a]volume={ac['sfx_level_db']}dB[fx]")
        mix_in.append("[fx]")
        idx += 1
    tr = cfg["transitions"]
    fc = ";".join(fc_parts) + ";" + "".join(mix_in) + \
        f"amix=inputs={len(mix_in)}:normalize=0:duration=first,atrim=0:{dur:.3f}," \
        f"afade=t=in:d={tr['audio_fade_in']}:curve=hsin," \
        f"afade=t=out:st={max(0, dur - tr['audio_fade_out']):.3f}:d={tr['audio_fade_out']}:curve=hsin[mx]"
    mixed = tmp / "mixed.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", fc,
         "-map", "[mx]", "-ar", str(SR), mixed], desc="mixagem")
    # 5) loudnorm em 2 passagens
    m = _measure_loudnorm(mixed, cfg)
    target = ac["target_lufs"] + gain_trim_db
    final = tmp / "audio_final.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", mixed, "-af",
         f"loudnorm=I={target}:TP={ac['true_peak']}:LRA={ac['lra']}:measured_I={m['input_i']}:"
         f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
         f"offset={m['target_offset']}:linear=true,aresample={SR}", "-ar", str(SR), final], desc="loudnorm")
    plan["_audio_report"] = {"voice_chain": chain, "music": str(music_file) if music_file else None,
                             "music_generated": plan["music"]["enabled"] and plan["music"]["source"] != "library",
                             "loudness_measured_pre": m["input_i"], "target_lufs": target}
    return final


def _shift_fc(iv: list[list[float]], w0: float, fade: float = 0.03) -> str:
    """Corta a voz nos intervalos com fades de ~30 ms em cada emenda (sem estalos nem 'degraus')."""
    parts, labels = [], []
    for i, (a, b) in enumerate(iv):
        a2, b2 = a - w0, b - w0
        d = b2 - a2
        f = min(fade, d / 4)
        parts.append(f"[0:a]atrim=start={a2:.4f}:end={b2:.4f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={f:.3f}:curve=hsin,afade=t=out:st={max(0, d - f):.4f}:d={f:.3f}:curve=hsin[a{i}]")
        labels.append(f"[a{i}]")
    return ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(iv)}:v=0:a=1[v]"


def check_music_license(plan: dict, cfg: dict) -> dict:
    """Usado pelo validador: trilha gerada = ok; biblioteca exige licença registrada."""
    mu = plan["music"]
    if not mu["enabled"]:
        return {"status": "sem_trilha"}
    if mu["source"] != "library":
        return {"status": "gerada_localmente", "ok": True}
    lib = cfg["audio"].get("music_library_dir")
    lic = read_json(Path(lib) / "licenses.json", {}) if lib else {}
    name = Path(mu.get("file") or "").name
    entry = lic.get(name)
    return {"status": "biblioteca", "ok": bool(entry and entry.get("commercial_youtube")), "license": entry,
            "file": name}
