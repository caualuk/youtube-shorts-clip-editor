"""Etapa 2 — transcrição completa com timestamps por palavra.

* Processa o áudio em blocos (padrão 10 min) extraídos pelo FFmpeg para WAV 16 kHz mono;
  cada bloco é salvo em transcricao/chunks/ e pulado na retomada.
* Motores: faster-whisper (recomendado: CTranslate2, 4x mais rápido que o Whisper original,
  menos memória, timestamps por palavra) ou openai-whisper como alternativa.
* Importação: se você já tem legendas (SRT/VTT) ou um JSON de transcrição, use --transcript;
  os tempos por palavra são então estimados proporcionalmente dentro de cada segmento
  (marcado como "word_timing": "estimated" — o validador avisa sobre isso).
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from .core import PipelineError, log, read_json, run, write_json

# ------------------------------------------------------------------ import

_TS = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{1,3})")


def _ts(s: str) -> float:
    m = _TS.search(s)
    if not m:
        parts = s.strip().split(":")
        if len(parts) == 2:  # VTT mm:ss.mmm
            mm, rest = parts
            return int(mm) * 60 + float(rest.replace(",", "."))
        raise ValueError(f"timestamp inválido: {s}")
    h, m_, s_, ms = m.groups()
    return int(h) * 3600 + int(m_) * 60 + int(s_) + int(ms.ljust(3, "0")) / 1000


def _estimate_words(text: str, start: float, end: float) -> list[dict]:
    toks = text.split()
    if not toks:
        return []
    weights = [max(1, len(re.sub(r"\W", "", t))) + 1 for t in toks]
    total = sum(weights)
    dur = max(0.05, end - start)
    out, t = [], start
    for tok, wgt in zip(toks, weights):
        d = dur * wgt / total
        out.append({"w": tok, "start": round(t, 3), "end": round(t + d * 0.92, 3), "prob": None})
        t += d
    return out


def import_transcript(path: Path) -> dict:
    path = Path(path)
    raw = path.read_text(encoding="utf-8-sig")
    segments = []
    if path.suffix.lower() == ".json":
        data = read_json(path)
        segs = data.get("segments", data) if isinstance(data, dict) else data
        for s in segs:
            words = s.get("words")
            if words:
                words = [{"w": w.get("w") or w.get("word", "").strip(), "start": float(w["start"]),
                          "end": float(w["end"]), "prob": w.get("prob") or w.get("probability")} for w in words]
            segments.append({"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip(),
                             "words": words or _estimate_words(s["text"], float(s["start"]), float(s["end"]))})
        timing = "native" if any(s.get("words") for s in segs) else "estimated"
    else:  # SRT / VTT
        blocks = re.split(r"\n\s*\n", raw.replace("\r", ""))
        for b in blocks:
            lines = [l for l in b.strip().split("\n") if l.strip()]
            idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
            if idx is None:
                continue
            a, bb = lines[idx].split("-->")
            st, en = _ts(a), _ts(bb.split()[0])
            text = re.sub(r"<[^>]+>", "", " ".join(lines[idx + 1:])).strip()
            if text:
                segments.append({"start": st, "end": en, "text": text, "words": _estimate_words(text, st, en)})
        timing = "estimated"
    for i, s in enumerate(segments):
        s["id"] = i
        s.setdefault("avg_logprob", None)
        s.setdefault("no_speech_prob", None)
    return {"language": None, "engine": f"import:{path.suffix.lower()}", "word_timing": timing, "segments": segments}


# ------------------------------------------------------------------ whisper

def _load_engine(cfg: dict):
    tc = cfg["transcription"]
    engine = tc["engine"]
    if engine == "faster-whisper":
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise PipelineError("faster-whisper não instalado: pip install faster-whisper") from e
        device = tc["device"]
        if device == "auto":
            try:
                import ctranslate2
                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        ctype = tc["compute_type"]
        if ctype == "auto":
            ctype = "float16" if device == "cuda" else "int8"
        log.info("Carregando faster-whisper '%s' (%s/%s)…", tc["model"], device, ctype)
        try:
            model = WhisperModel(tc["model"], device=device, compute_type=ctype)
        except Exception as e:
            raise PipelineError(
                f"Não foi possível carregar o modelo Whisper '{tc['model']}': {e}\n"
                "Na primeira execução o modelo é baixado do Hugging Face; verifique a conexão "
                "ou aponte transcription.model para uma pasta local com o modelo, "
                "ou use --transcript com um SRT/VTT existente.") from e

        def transcribe(wav: Path):
            segs, info = model.transcribe(str(wav), language=tc["language"], word_timestamps=True,
                                          vad_filter=tc["vad_filter"], beam_size=tc["beam_size"],
                                          condition_on_previous_text=False)
            out = []
            for s in segs:
                out.append({"start": s.start, "end": s.end, "text": s.text.strip(),
                            "avg_logprob": s.avg_logprob, "no_speech_prob": s.no_speech_prob,
                            "words": [{"w": w.word.strip(), "start": w.start, "end": w.end,
                                       "prob": round(w.probability, 3)} for w in (s.words or [])]})
            return out, info.language
        return transcribe
    if engine == "openai-whisper":
        try:
            import whisper
        except ImportError as e:
            raise PipelineError("openai-whisper não instalado: pip install openai-whisper") from e
        model = whisper.load_model(tc["model"])

        def transcribe(wav: Path):
            r = model.transcribe(str(wav), language=tc["language"], word_timestamps=True,
                                 condition_on_previous_text=False)
            out = [{"start": s["start"], "end": s["end"], "text": s["text"].strip(),
                    "avg_logprob": s.get("avg_logprob"), "no_speech_prob": s.get("no_speech_prob"),
                    "words": [{"w": w["word"].strip(), "start": w["start"], "end": w["end"],
                               "prob": round(w.get("probability", 0), 3)} for w in s.get("words", [])]}
                   for s in r["segments"]]
            return out, r.get("language")
        return transcribe
    raise PipelineError(f"Motor de transcrição desconhecido: {engine}")


def transcribe_video(video: Path, project: Path, cfg: dict, duration: float) -> dict:
    tc = cfg["transcription"]
    chunk_len, ov = float(tc["chunk_seconds"]), float(tc["chunk_overlap_seconds"])
    n = max(1, math.ceil(duration / chunk_len))
    cdir = project / "transcricao" / "chunks"
    cdir.mkdir(parents=True, exist_ok=True)
    engine = None
    language = tc["language"]
    for i in range(n):
        cj = cdir / f"chunk_{i:04d}.json"
        if cj.exists():
            log.info("Bloco %d/%d já transcrito — pulando (retomada).", i + 1, n)
            continue
        start = i * chunk_len
        length = min(chunk_len + ov, duration - start)
        wav = project / "tmp" / f"chunk_{i:04d}.wav"
        wav.parent.mkdir(exist_ok=True)
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav], desc=f"extrai áudio bloco {i}")
        if engine is None:
            engine = _load_engine(cfg)
        log.info("Transcrevendo bloco %d/%d (%.0fs–%.0fs)…", i + 1, n, start, start + length)
        segs, lang = engine(wav)
        language = language or lang
        for s in segs:
            s["start"] += start
            s["end"] += start
            for w in s["words"]:
                w["start"] = round(w["start"] + start, 3)
                w["end"] = round(w["end"] + start, 3)
        write_json(cj, {"index": i, "start": start, "nominal_end": start + chunk_len, "language": lang, "segments": segs})
        wav.unlink(missing_ok=True)

    # junta blocos, descartando palavras duplicadas na sobreposição
    segments = []
    for i in range(n):
        c = read_json(cdir / f"chunk_{i:04d}.json")
        lo = c["start"] + (ov / 2 if i > 0 else 0)
        hi = c["nominal_end"] + ov / 2 if i < n - 1 else 1e12
        for s in c["segments"]:
            words = [w for w in s["words"] if lo <= w["start"] < hi]
            if not words:
                continue
            s = dict(s, words=words, start=words[0]["start"], end=words[-1]["end"],
                     text=" ".join(w["w"] for w in words))
            segments.append(s)
            language = language or c.get("language")
    for i, s in enumerate(segments):
        s["id"] = i
    return {"language": language, "engine": f"{tc['engine']}:{tc['model']}", "word_timing": "native",
            "segments": segments}


def save_transcript(tr: dict, project: Path) -> None:
    d = project / "transcricao"
    write_json(d / "transcript.json", tr)
    from .core import fmt_ts
    srt = []
    for i, s in enumerate(tr["segments"], 1):
        srt.append(f"{i}\n{fmt_ts(s['start']).replace('.', ',')} --> {fmt_ts(s['end']).replace('.', ',')}\n{s['text']}\n")
    (d / "transcript.srt").write_text("\n".join(srt), encoding="utf-8")
    (d / "transcript.txt").write_text(
        "\n".join(f"[{fmt_ts(s['start'])}] {s['text']}" for s in tr["segments"]), encoding="utf-8")
