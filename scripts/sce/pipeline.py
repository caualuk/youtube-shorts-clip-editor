"""Orquestrador: etapas idempotentes com checkpoint em state.json."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import numpy as np

from . import lexicon as lx
from .analyze import analyze, candidates_markdown
from .core import (PROJECT_DIRS, PipelineError, ProjectState, load_config, log, read_json, require_binaries,
                   write_json)
from .faces import sample_face_presence
from .plan import STYLE_ORDER, build_plan, to_output_time
from .probe import probe_video
from .render import render_clip
from .report import write_reports
from .selection import auto_select, llm_select, validate_selection
from .transcribe import import_transcript, save_transcript, transcribe_video
from .validator import uniformity, validate_clip

STAGES = ["probe", "transcribe", "analyze", "select", "plan", "render", "validate", "report"]


class Project:
    def __init__(self, root: Path, config_extra: Path | None = None):
        self.root = Path(root).resolve()
        self.cfg = load_config(self.root, config_extra)
        self.cfg["_project"] = str(self.root)
        self.state = ProjectState(self.root)

    @property
    def meta(self) -> dict:
        return self.state.meta

    def p(self, *parts) -> Path:
        return self.root.joinpath(*parts)

    # ------------------------------------------------------------- init
    @classmethod
    def init(cls, root: Path, video: Path, rights: str, note: str | None, on_screen: bool,
             copy: bool = False, content_type: str = "auto", facecam: str = "auto",
             content_notes: str | None = None) -> "Project":
        root = Path(root).resolve()
        for d in PROJECT_DIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        video = Path(video).resolve()
        if not video.exists():
            raise PipelineError(f"Vídeo não encontrado: {video}")
        dst = root / "video_original" / video.name
        if not dst.exists():
            if copy:
                shutil.copy2(video, dst)
            else:
                try:
                    os.symlink(video, dst)
                except OSError:
                    shutil.copy2(video, dst)
        write_json(root / "rights.json", {"status": rights, "note": note, "creator_on_screen": on_screen,
                                          "declared_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        write_json(root / "content.json", {"type": content_type, "facecam": facecam, "notes": content_notes})
        prj = cls(root)
        prj.state.meta["video"] = str(dst)
        prj.state.save()
        return prj

    @property
    def video(self) -> Path:
        v = self.meta.get("video")
        if not v:
            raise PipelineError("Projeto sem vídeo. Rode 'init' primeiro.")
        return Path(v)

    @property
    def content(self) -> dict:
        """Tipo de conteúdo informado no prompt/CLI (reacao, podcast, gameplay, aula, entrevista, vlog, auto)."""
        c = dict(self.cfg.get("content", {}))
        c.update(read_json(self.p("content.json"), {}))
        return c

    @property
    def rights(self) -> dict:
        return read_json(self.p("rights.json"), {"status": "unknown"})

    # ------------------------------------------------------------- stages
    def stage_probe(self, force=False):
        if self.state.done("probe") and not force:
            return
        require_binaries()
        info = probe_video(self.video)
        fps = info["fps"] or 30.0
        fps = min(fps, self.cfg["render"]["max_fps"])
        if self.cfg["render"]["fps"]:
            fps = float(self.cfg["render"]["fps"])
        info["render_fps"] = round(fps, 3)
        write_json(self.p("analise", "video_info.json"), info)
        self.state.meta.update({k: info[k] for k in ("duration", "width", "height", "fps", "render_fps")})
        self.state.meta["audio_quality"] = info["audio_quality"]
        self.state.mark("probe", "done")
        log.info("Vídeo: %.1fs %dx%d @%.2ffps — %s", info["duration"], info["width"], info["height"], info["fps"],
                 "; ".join(info["warnings"]) or "sem avisos")

    def stage_transcribe(self, transcript: Path | None = None, force=False):
        if self.state.done("transcribe") and not force:
            return
        if transcript:
            tr = import_transcript(transcript)
            log.info("Transcrição importada: %d segmentos (%s).", len(tr["segments"]), tr["word_timing"])
        else:
            tr = transcribe_video(self.video, self.root, self.cfg, self.meta["duration"])
        if not tr["segments"]:
            raise PipelineError("Transcrição vazia: o vídeo não tem fala detectável.")
        save_transcript(tr, self.root)
        self.state.mark("transcribe", "done", engine=tr["engine"], word_timing=tr["word_timing"])

    def stage_analyze(self, force=False):
        if self.state.done("analyze") and not force:
            return
        tr = read_json(self.p("transcricao", "transcript.json"))
        an = analyze(tr, self.video, self.meta, self.cfg)
        # amostragem de rostos só para os melhores candidatos (seek, sem decodificar tudo)
        top = an["candidates"][: self.cfg["clips"]["max_candidates_exported"]]
        from .faces import FaceDetector
        det = FaceDetector()
        n = self.cfg["analysis"]["face_sample_frames_per_candidate"]
        for c in top:
            times = list(np.linspace(c["start"] + 0.5, c["end"] - 0.5, n))
            c["face"] = sample_face_presence(self.video, times, det)
            c["scores"]["qualidade_av"] = round(c["scores"]["qualidade_av"] * 0.8 + 20 * min(1, c["face"]["face_ratio"] + 0.3), 1)
        sents_full = [{"id": s["id"], "start": s["start"], "end": s["end"], "terminal": s["terminal"],
                       "words": s["words"]} for s in an.pop("sentences_full")]
        write_json(self.p("analise", "sentences_words.json"), sents_full)
        for c in an["candidates"]:
            c.pop("words", None)
        an["candidates"] = an["candidates"][: max(200, self.cfg["clips"]["max_candidates_exported"])]
        write_json(self.p("analise", "analysis.json"), an)
        (self.p("analise", "candidates.md")).write_text(
            candidates_markdown(an, self.cfg["clips"]["max_candidates_exported"]), encoding="utf-8")
        self.state.mark("analyze", "done", n_candidates=len(an["candidates"]))
        log.info("%d candidatos gerados → analise/candidates.md", len(an["candidates"]))

    def stage_select(self, mode: str = "file", force=False):
        if self.state.done("select") and not force:
            return
        an = read_json(self.p("analise", "analysis.json"))
        sents = read_json(self.p("analise", "sentences_words.json"))
        sel_file = self.p("analise", "selection.json")
        notes = []
        if mode == "file" and sel_file.exists():
            items = read_json(sel_file)["clips"]
            log.info("Usando seleção editorial de analise/selection.json (%d itens).", len(items))
        elif mode == "llm":
            md = (self.p("analise", "candidates.md")).read_text(encoding="utf-8")
            items = llm_select(md, self.cfg, self.state)
        elif mode in ("auto", "file"):
            if mode == "file":
                log.warning("analise/selection.json não existe — usando seleção automática heurística.")
            chosen, notes = auto_select(an["candidates"], self.cfg)
            items = [{"s0": c["s0"], "s1": c["s1"], "justification": c["justification"], "source": "auto",
                      "score": c["auto_score"], "scores": c["scores"], "keywords": c["keywords"],
                      "topic": c["topic"]} for c in chosen]
        else:
            raise PipelineError(f"Modo de seleção inválido: {mode}")
        clips, vnotes = validate_selection(items, sents, self.cfg)
        notes += vnotes
        # anexa dados de rosto/score dos candidatos equivalentes
        by_span = {(c["s0"], c["s1"]): c for c in an["candidates"]}
        for c in clips:
            ref = by_span.get((c["s0"], c["s1"]))
            if ref:
                c.setdefault("face", ref.get("face"))
                if not c.get("scores"):
                    c["scores"], c["auto_score"] = ref["scores"], ref["auto_score"]
                if not c.get("keywords"):
                    c["keywords"] = ref["keywords"]
                c["topic"] = ref["topic"]
            if not c.get("keywords"):
                c["keywords"] = lx.content_words(c["text"])[:5]
            if not c.get("face"):
                times = list(np.linspace(c["start"] + 0.5, c["end"] - 0.5, 6))
                c["face"] = sample_face_presence(self.video, times)
        write_json(self.p("analise", "selected_clips.json"), {"clips": clips, "notes": notes})
        self.write_auto_editorial(clips, sents, an["topics"])
        self.state.mark("select", "done", n=len(clips), mode=mode)
        for n_ in notes:
            log.warning(n_)
        log.info("%d cortes selecionados.", len(clips))

    def write_auto_editorial(self, clips: list[dict], sents: list[dict], topics: list[dict]) -> dict:
        """Elementos de edição derivados da transcrição → analise/editorial_auto.json (revisável)."""
        from .auto_editorial import build_auto_editorial
        used: set = set()
        out = {f"{i:02d}": build_auto_editorial(c, sents, topics, used) for i, c in enumerate(clips, 1)}
        write_json(self.p("analise", "editorial_auto.json"), {"clips": out})
        log.info("Elementos de edição gerados da transcrição → analise/editorial_auto.json")
        return out

    def _facecam_for(self, clip: dict) -> dict | None:
        """Procura a câmera de reação no trecho. Em 'auto', só aceita se parecer mesmo uma facecam
        (rosto pequeno, fora do centro e parado); em 'reacao', aceita qualquer rosto estável ou a dica de canto."""
        ct = self.content
        ctype, hint = ct.get("type", "auto"), ct.get("facecam", "auto")
        if ctype not in ("reacao", "auto") or self.meta["height"] >= self.meta["width"]:
            return None
        from .faces import detect_facecam
        bot = 1 - self.cfg["camera"]["split_content"]
        aspect = self.cfg["render"]["width"] / (self.cfg["render"]["height"] * bot)
        fc = detect_facecam(self.video, clip["start"], clip["end"], self.meta["width"], self.meta["height"],
                            hint if ctype == "reacao" else "auto", aspect)
        if fc and ctype == "auto":
            f = fc.get("face")
            if not f or f[2] > 0.12 or abs(f[0] - 0.5) < 0.2:
                return None
        return fc

    def _background_audio_flag(self, clip: dict) -> bool:
        an = read_json(self.p("analise", "analysis.json"))
        e = np.array(an["energy_db"], dtype=np.float32)
        win = an["energy_window"]
        words = clip["words"]
        speech, gaps = [], []
        for w in words:
            speech += list(e[int(w["start"] / win): int(w["end"] / win) + 1])
        for a, b in zip(words, words[1:]):
            if b["start"] - a["end"] > 0.8:  # só janelas inteiramente dentro da pausa
                i0 = int(np.ceil((a["end"] + 0.1) / win))
                i1 = int(np.floor((b["start"] - 0.1) / win))
                gaps += list(e[i0:i1])
        if len(gaps) < 2 or not speech:
            return False
        g, s = float(np.median(gaps)), float(np.median(speech))
        return g > -38 and (s - g) < 14

    def stage_plan(self, only: set | None = None, style_overrides: dict | None = None):
        sel = read_json(self.p("analise", "selected_clips.json"))
        editorial = read_json(self.p("analise", "editorial.json"), {"clips": {}}).get("clips", {})
        auto_ed = read_json(self.p("analise", "editorial_auto.json"), {"clips": {}}).get("clips", {})
        pdir = self.p("analise", "plans")
        pdir.mkdir(exist_ok=True)
        plans = []
        for i, c in enumerate(sel["clips"], 1):
            cid = f"{i:02d}"
            if only and cid not in only:
                existing = read_json(pdir / f"corte_{cid}.json")
                if existing:
                    plans.append(existing)
                    continue
            # editorial.json (revisado pelo Claude/usuário) tem prioridade; senão, o gerado da transcrição
            ed = editorial.get(cid) or editorial.get(str(i)) or auto_ed.get(cid) or {}
            ov = (style_overrides or {}).get(cid) or {}
            facecam = self._facecam_for(c)
            plan = build_plan(i, c, ed, c.get("face") or {}, self.meta, self.cfg, ov.get("style"), ov.get("variant"),
                              self.content.get("type", "auto"), facecam)
            plan["_source_crop_height"] = min(self.meta["height"], self.cfg["render"]["decode_height"])
            sents_meta = read_json(self.p("analise", "analysis.json"))["sentences"]
            lps = [sents_meta[k]["avg_logprob"] for k in range(c["s0"], c["s1"] + 1)
                   if k < len(sents_meta) and sents_meta[k].get("avg_logprob") is not None]
            plan["_avg_logprob"] = float(np.mean(lps)) if lps else None
            plan["_bg_audio"] = self._background_audio_flag(c)
            plan["_words_src"] = c["words"]
            old = read_json(pdir / f"corte_{cid}.json")
            if old and self._plan_hash(old) == self._plan_hash(plan):
                # plano idêntico: preserva metadados da última renderização (cartões agendados, áudio, etc.)
                for k, v in old.items():
                    if k.startswith("_") and k not in plan:
                        plan[k] = v
            write_json(pdir / f"corte_{cid}.json", plan)
            plans.append(plan)
        self.state.mark("plan", "done", n=len(plans))
        return plans

    def _render_one(self, plan: dict, gain_trim: float = 0.0):
        cid = plan["id"]
        out = self.p("cortes", f"corte_{cid}.mp4")
        tmp = self.p("tmp", f"corte_{cid}")
        if plan.get("censor_words"):
            cw = set(lx.norm(w) for w in plan["censor_words"])
            plan["_censor_times"] = [(to_output_time(w["start"], plan["intervals"]),
                                      to_output_time(w["end"], plan["intervals"]))
                                     for w in plan["_words_src"]
                                     if lx.norm("".join(ch for ch in w["w"] if ch.isalnum())) in cw]
        t0 = time.time()
        info = render_clip(self.video, plan, out, tmp, self.cfg, self.meta, gain_trim)
        plan["_render"] = dict(info, seconds=round(time.time() - t0, 1))
        write_json(self.p("analise", "plans", f"corte_{cid}.json"), plan)
        log.info("Corte %s renderizado (%.1fs, %d quadros, %.0fs de processamento).", cid, plan["duration"],
                 info["frames"], time.time() - t0)
        shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _plan_hash(plan: dict) -> str:
        import hashlib, json
        core = {k: v for k, v in plan.items() if not k.startswith("_")}
        return hashlib.md5(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()

    def stage_render(self, only: set | None = None, force=False):
        """Renderiza; na retomada pula cortes cujo plano não mudou (hash) e cujo MP4 existe."""
        plans = self.stage_plan(only)
        done = dict(self.state.data["stages"].get("render", {}).get("clips", {}) or {})
        if isinstance(done, list):
            done = {}
        for p in plans:
            if only and p["id"] not in only:
                continue
            h = self._plan_hash(p)
            if not force and done.get(p["id"]) == h and self.p("cortes", f"corte_{p['id']}.mp4").exists():
                log.info("Corte %s já renderizado com este plano — pulando (retomada).", p["id"])
                continue
            self._render_one(p)
            done[p["id"]] = h
            self.state.mark("render", "partial", clips=done)
        self.state.mark("render", "done", clips=done)

    def _validate_all(self, plans: list[dict]) -> dict:
        uni = uniformity(plans)
        tr_state = self.state.data["stages"].get("transcribe", {})
        vals = {}
        for p in plans:
            tinfo = {"word_timing": tr_state.get("word_timing"), "clip_avg_logprob": p.get("_avg_logprob"),
                     "source_has_background_audio": p.get("_bg_audio")}
            vals[p["id"]] = validate_clip(self.p("cortes", f"corte_{p['id']}.mp4"), p, self.rights, self.cfg,
                                          self.meta, tinfo, uni)
        return vals, uni

    def stage_validate(self):
        """Valida, tenta autocorrigir o que é corrigível, re-renderiza e revalida."""
        pdir = self.p("analise", "plans")
        sel = read_json(self.p("analise", "selected_clips.json"))
        plans = [read_json(pdir / f"corte_{i:02d}.json") for i in range(1, len(sel["clips"]) + 1)]
        history = {p["id"]: 0 for p in plans}
        vals, uni = self._validate_all(plans)
        for rnd in range(self.cfg["validator"]["max_autofix_rounds"]):
            todo = {cid: v["acoes_autofix"] for cid, v in vals.items() if v["acoes_autofix"]}
            if not todo:
                break
            log.info("Autocorreção rodada %d: %s", rnd + 1, {k: [str(a) for a in v] for k, v in todo.items()})
            vary = any("vary_style" in acts for acts in todo.values())
            if vary:  # redistribui estilos para quebrar a uniformidade do lote
                # rodízio determinístico: cortes vizinhos nunca repetem estilo, animação de título ou abertura
                used = {}
                for k, p in enumerate(plans):
                    used[p["id"]] = {"style": STYLE_ORDER[k % len(STYLE_ORDER)],
                                     "variant": {"hook_anim": ["fade_up", "fade_scale"][k % 2],
                                                 "card_side": ["left", "right"][k % 2]}}
                plans = self.stage_plan(None, used)
                for p in plans:
                    todo.setdefault(p["id"], [])
            for p in plans:
                acts = todo.get(p["id"])
                if acts is None:
                    continue
                gain = 0.0
                for a in acts:
                    if isinstance(a, tuple) and a[0] == "gain":
                        gain += a[1]
                    elif a == "censor_strong":
                        p["censor_words"] = sorted(set(p.get("censor_words", [])) |
                                                   set(lx.profanity_hits(p["text"])["strong"]))
                    elif a == "music_generated":
                        p["music"]["source"] = "generated"
                    elif a == "fit_duration":
                        from .plan import fit_duration
                        p["intervals"] = fit_duration(p["intervals"], self.cfg, self.meta["duration"])
                        p["duration"] = round(sum(b - a_ for a_, b in p["intervals"]), 3)
                self._render_one(p, gain)
                history[p["id"]] += 1
            vals, uni = self._validate_all(plans)
        write_json(self.p("relatorios", "validacao.json"), {"uniformidade": uni, "cortes": vals,
                                                            "rodadas_autofix": history})
        self.state.mark("validate", "done")
        return plans, vals, uni, history

    def stage_report(self, plans=None, vals=None, uni=None, history=None):
        if plans is None:
            sel = read_json(self.p("analise", "selected_clips.json"))
            plans = [read_json(self.p("analise", "plans", f"corte_{i:02d}.json"))
                     for i in range(1, len(sel["clips"]) + 1)]
            v = read_json(self.p("relatorios", "validacao.json"), {})
            vals, uni, history = v.get("cortes", {}), v.get("uniformidade", 0), v.get("rodadas_autofix", {})
        sel = read_json(self.p("analise", "selected_clips.json"))
        info = read_json(self.p("analise", "video_info.json"))
        write_reports(self.root, info, plans, vals, sel.get("notes", []), self.rights, uni, history)
        self.state.mark("report", "done")
        log.info("Relatórios em %s", self.p("relatorios"))
