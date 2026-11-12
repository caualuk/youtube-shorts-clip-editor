#!/usr/bin/env python3
"""CLI do AI YouTube Clip Editor & Monetization Validator.

Fluxo recomendado (com o Claude fazendo a parte editorial):
  python run_pipeline.py init     --project P --video V --rights own --creator-on-screen
  python run_pipeline.py analyze  --project P            # probe + transcrição + análise
  (Claude lê P/analise/candidates.md e escreve P/analise/selection.json e P/analise/editorial.json)
  python run_pipeline.py produce  --project P            # seleção → planos → render → validação → relatórios

Totalmente automático (sem contribuição editorial — o validador vai apontar isso):
  python run_pipeline.py run --project P --video V --rights own --auto

Outros: status | select | render [--only 01,04] | validate | report | reset --from render
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sce.core import PipelineError, setup_logging  # noqa: E402
from sce.pipeline import STAGES, Project  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["init", "analyze", "select", "render", "validate", "report", "produce",
                                        "run", "status", "reset"])
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--rights", choices=["own", "licensed", "third_party", "unknown"], default="unknown",
                    help="Titularidade do vídeo original (essencial para o validador).")
    ap.add_argument("--rights-note", help="Descrição da licença/autorização (escopo, data, prova).")
    ap.add_argument("--creator-on-screen", action="store_true",
                    help="O próprio dono do canal aparece/fala no vídeo original.")
    ap.add_argument("--content-type", default="auto",
                    choices=["auto", "reacao", "podcast", "entrevista", "gameplay", "aula", "vlog"],
                    help="Tipo de vídeo. 'reacao' = tela dividida conteúdo + câmera de quem reage.")
    ap.add_argument("--facecam", default="auto",
                    choices=["auto", "top-left", "top-right", "bottom-left", "bottom-right"],
                    help="Canto da câmera de reação (ajuda quando a detecção automática falhar).")
    ap.add_argument("--content-notes", help="Descrição livre do vídeo (ex.: 'react de gameplay de terror').")
    ap.add_argument("--copy-video", action="store_true", help="Copiar (em vez de link simbólico) o original.")
    ap.add_argument("--transcript", type=Path, help="SRT/VTT/JSON existente (pula o Whisper).")
    ap.add_argument("--auto", action="store_true", help="Seleção heurística automática.")
    ap.add_argument("--llm", action="store_true", help="Seleção via API (exige llm.enabled e ANTHROPIC_API_KEY).")
    ap.add_argument("--only", help="IDs de cortes, ex.: 01,04")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--from", dest="from_stage", choices=STAGES)
    ap.add_argument("--config", type=Path, help="JSON com overrides de configuração.")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    a.project.mkdir(parents=True, exist_ok=True)
    log = setup_logging(a.project, a.verbose)
    try:
        if a.command in ("init", "run"):
            if not a.video:
                ap.error("--video é obrigatório para init/run")
            Project.init(a.project, a.video, a.rights, a.rights_note, a.creator_on_screen, a.copy_video,
                         a.content_type, a.facecam, a.content_notes)
            log.info("Projeto inicializado em %s (direitos: %s, tipo: %s)", a.project, a.rights, a.content_type)
            if a.command == "init":
                return 0
        prj = Project(a.project, a.config)
        only = set(x.strip().zfill(2) for x in a.only.split(",")) if a.only else None
        mode = "llm" if a.llm else ("auto" if a.auto else "file")
        if a.command == "status":
            for s in STAGES:
                st = prj.state.data["stages"].get(s, {})
                print(f"{s:<11} {st.get('status', '-'):<8} {st.get('at', '')}")
            print("LLM:", prj.state.data.get("llm_usage"))
            return 0
        if a.command == "reset":
            prj.state.reset_from(STAGES, a.from_stage or "select")
            log.info("Etapas a partir de '%s' marcadas para refazer.", a.from_stage or "select")
            return 0
        if a.command in ("analyze", "run"):
            prj.stage_probe(a.force)
            prj.stage_transcribe(a.transcript, a.force)
            prj.stage_analyze(a.force)
            if a.command == "analyze":
                log.info("Próximo passo: ler %s e escrever selection.json/editorial.json (ou usar --auto).",
                         prj.p("analise", "candidates.md"))
                return 0
        if a.command in ("select", "produce", "run"):
            prj.stage_select(mode, force=a.force or a.command == "select")
            if a.command == "select":
                return 0
        if a.command in ("render", "produce", "run"):
            prj.stage_render(only, a.force)
        if a.command in ("validate", "produce", "run"):
            plans, vals, uni, hist = prj.stage_validate()
            prj.stage_report(plans, vals, uni, hist)
            for cid, v in vals.items():
                log.info("Corte %s: %s (interno %d/100)", cid, v["classificacao"], v["pontuacao_interna"])
            return 0
        if a.command == "report":
            prj.stage_report()
        return 0
    except PipelineError as e:
        log.error("%s", e)
        return 2
    except KeyboardInterrupt:
        log.warning("Interrompido. Rode o mesmo comando para retomar do último checkpoint.")
        return 130
    except Exception:
        log.error("Erro inesperado:\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
