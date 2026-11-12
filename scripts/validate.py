#!/usr/bin/env python3
"""Monetization Compliance Validator standalone: revalida os cortes de um projeto sem re-renderizar.

  python validate.py --project P            # revalida e reescreve os relatórios
  python validate.py --project P --json     # imprime o resultado em JSON
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sce.core import read_json, setup_logging, write_json  # noqa: E402
from sce.pipeline import Project  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    setup_logging(a.project)
    prj = Project(a.project)
    sel = read_json(prj.p("analise", "selected_clips.json"))
    plans = [read_json(prj.p("analise", "plans", f"corte_{i:02d}.json")) for i in range(1, len(sel["clips"]) + 1)]
    vals, uni = prj._validate_all(plans)
    prev = read_json(prj.p("relatorios", "validacao.json"), {})
    hist = prev.get("rodadas_autofix", {})
    write_json(prj.p("relatorios", "validacao.json"), {"uniformidade": uni, "cortes": vals, "rodadas_autofix": hist})
    prj.stage_report(plans, vals, uni, hist)
    if a.json:
        print(json.dumps(vals, ensure_ascii=False, indent=2, default=str))
    else:
        for cid, v in vals.items():
            print(f"corte_{cid}: {v['classificacao']:<16} interno {v['pontuacao_interna']:>3}/100  "
                  f"problemas={len(v['problemas'])} revisão_humana={len(v['revisao_humana'])}")
        print(f"uniformidade do lote: {uni:.0%}")


if __name__ == "__main__":
    main()
