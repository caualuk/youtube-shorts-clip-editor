"""Relatórios finais: relatorio_geral.md, validacao_monetizacao.md, timestamps.json, score_cortes.json."""
from __future__ import annotations

import time
from pathlib import Path

from .core import fmt_ts, write_json

SHORT = {"BAIXO RISCO": "Baixo", "RISCO MODERADO": "Moderado", "ALTO RISCO": "Alto", "NÃO RECOMENDADO": "Não recomendado"}
STATUS = {0: "Revisado", 1: "Revisão necessária", 2: "Correção necessária", 3: "Não publicar"}

DISCLAIMER = ("> **Aviso:** esta avaliação é técnica e preventiva. Ela não garante aprovação, monetização "
              "ou ausência de reivindicações. A decisão é do YouTube e depende do canal como um todo, dos "
              "direitos sobre o material e de fatores que a ferramenta não consegue verificar. A pontuação "
              "0–100 é apenas para organização interna.")


def _techniques(plan: dict) -> list[str]:
    names = {"reaction": "tela dividida conteúdo + reação (diretor equilibrando os dois)",
             "content": "prioridade ao conteúdo (segue a ação)", "face": "rosto acompanhado", "fill": "vertical"}
    t = [f"layout: {names.get(plan['layout'], plan['layout'])}", f"estilo '{plan['style']}'", "reenquadramento 9:16"]
    if plan.get("_split_stats"):
        s_ = plan["_split_stats"]
        t.append(f"divisão conteúdo/reação variando suavemente entre {s_['min']:.0%} e {s_['max']:.0%} de conteúdo")
    if len(plan["intervals"]) > 1:
        t.append(f"{len(plan['intervals']) - 1} jump cut(s) com dissolve")
    if plan["camera"]["punches"]:
        t.append(f"{len(plan['camera']['punches'])} zoom(s) suaves de ênfase")
    t.append("abertura com fade, cartões com fade/deslize, sem efeitos sonoros de transição")
    t.append("legendas dinâmicas palavra-a-palavra")
    if plan["music"]["enabled"]:
        t.append(f"trilha {'gerada' if plan['music']['source'] != 'library' else 'de biblioteca'} ({plan['music']['mood']}) com ducking")
    if plan.get("broll"):
        t.append(f"{len(plan['broll'])} B-roll/imagem")
    t.append("voz: EQ + compressão + loudnorm -14 LUFS")
    return t


def write_reports(project: Path, meta: dict, plans: list[dict], vals: dict, notes: list[str], rights: dict,
                  uniformity: float, history: dict) -> None:
    rd = project / "relatorios"
    rd.mkdir(exist_ok=True)
    ts, sc = [], []
    for p in plans:
        v = vals.get(p["id"], {})
        ts.append({"corte": p["id"], "arquivo": f"cortes/corte_{p['id']}.mp4",
                   "origem_inicio": round(p["source_start"], 3), "origem_fim": round(p["source_end"], 3),
                   "origem_inicio_hms": fmt_ts(p["source_start"]), "origem_fim_hms": fmt_ts(p["source_end"]),
                   "intervalos_mantidos": p["intervals"], "duracao_final": p["duration"]})
        sc.append({"corte": p["id"], "selecao_fonte": p["selection_source"], "score_selecao": p.get("auto_score"),
                   "scores_selecao": p.get("scores"), "risco": v.get("classificacao"),
                   "pontuacao_interna": v.get("pontuacao_interna"), "por_eixo": v.get("pontuacao_por_eixo")})
    write_json(rd / "timestamps.json", ts)
    write_json(rd / "score_cortes.json", sc)

    g = [f"# Relatório geral — {Path(meta['path']).name}", "",
         f"Gerado em {time.strftime('%Y-%m-%d %H:%M')}.", "",
         "## Vídeo original", "",
         f"- Duração: {fmt_ts(meta['duration'])} | {meta['width']}x{meta['height']} | {meta['fps']} fps | "
         f"{meta['video_codec']}/{meta['audio_codec']}",
         f"- Direitos declarados: **{rights.get('status', 'unknown')}**"
         + (f" — {rights['note']}" if rights.get("note") else ""), ""]
    if meta.get("warnings"):
        g += ["Avisos de entrada:"] + [f"- {w}" for w in meta["warnings"]] + [""]
    g += ["## Resumo", "", f"Cortes produzidos: **{len(plans)}**", ""]
    if notes:
        g += ["Limitações e observações da seleção:"] + [f"- {n}" for n in notes] + [""]
    g += ["| Corte | Duração | Tema | Risco | Status |", "|---|---|---|---|---|"]
    for p in plans:
        v = vals.get(p["id"], {})
        g.append(f"| {p['id']} | {p['duration']:.0f}s | {p['title'][:48]} | "
                 f"{SHORT.get(v.get('classificacao'), '—')} | {STATUS.get(v.get('nivel'), 'Não validado')} |")
    g += ["", DISCLAIMER, "", "## Detalhes por corte", ""]
    for p in plans:
        v = vals.get(p["id"], {})
        g += [f"### Corte {p['id']} — {p['title']}", "",
              f"- Arquivo: `cortes/corte_{p['id']}.mp4` — {p['duration']:.2f}s",
              f"- Trecho original: {fmt_ts(p['source_start'])} → {fmt_ts(p['source_end'])}",
              f"- Assunto: {p['topic_label']}",
              f"- Justificativa da seleção ({p['selection_source']}): {p['justification'] or '—'}",
              f"- Técnicas de edição: {'; '.join(_techniques(p))}",
              f"- Elementos originais adicionados: {', '.join(e['type'] for e in p['editorial']) or 'nenhum (apenas edição cosmética)'}",
              f"- Risco de monetização: **{v.get('classificacao', 'não validado')}** (interno {v.get('pontuacao_interna', '—')}/100)",
              f"- Direitos autorais: {'; '.join(v.get('riscos_direitos_autorais') or ['nenhum risco adicional identificado nos itens verificáveis'])}",
              f"- Rodadas de autocorreção: {history.get(p['id'], 0)}", "",
              f"> {p['text']}", ""]
    g += ["## Recomendações para publicação", "",
          "- Escreva título e descrição próprios explicando sua contribuição (o YouTube analisa título e descrição "
          "para entender como o conteúdo foi feito).",
          "- Publique de forma espaçada e varie formatos ao longo do canal (evita padrão de produção em massa).",
          "- Mantenha as provas de licença/autorização arquivadas.",
          "- Resolva todos os itens de 'revisão humana' do arquivo validacao_monetizacao.md antes de publicar.", ""]
    (rd / "relatorio_geral.md").write_text("\n".join(g), encoding="utf-8")

    m = ["# Validação de monetização (Monetization Compliance Validator)", "", DISCLAIMER, "",
         f"Similaridade de template entre os cortes do lote: {uniformity:.0%} "
         "(alto = risco de conteúdo inautêntico/produção em massa).", ""]
    for p in plans:
        v = vals.get(p["id"])
        if not v:
            m += [f"## Corte {p['id']} — NÃO VALIDADO", ""]
            continue
        md = v.get("medicoes") or {}
        m += [f"## Corte {p['id']} — {v['classificacao']} (interno {v['pontuacao_interna']}/100)", "",
              "1. **Classificação:** " + v["classificacao"],
              "2. **Pontuação interna:** " + ", ".join(f"{k} {val}" for k, val in v["pontuacao_por_eixo"].items()),
              "3. **Problemas encontrados:**"]
        m += [f"   - [{i['eixo']}/{i['severidade']}] {i['msg']}" for i in v["problemas"]] or ["   - nenhum"]
        m += ["4. **Motivos da classificação:**"] + [f"   - {r}" for r in v.get("motivos", [])]
        m += ["5. **Recomendações de correção:**"] + [f"   - {r}" for r in v["recomendacoes"]]
        m += ["6. **Melhorias de originalidade:**"] + ([f"   - {r}" for r in v["melhorias_originalidade"]] or ["   - —"])
        m += ["7. **Riscos de direitos autorais:**"] + ([f"   - {r}" for r in v["riscos_direitos_autorais"]] or ["   - nenhum identificado nos itens verificáveis"])
        m += ["8. **Exige revisão humana:**"] + ([f"   - {r}" for r in v["revisao_humana"]] or ["   - —"])
        if md:
            m += ["", f"Medições reais do arquivo: {md.get('vcodec')}/{md.get('acodec')} {md.get('width')}x{md.get('height')} "
                  f"@ {md.get('fps')} fps, {md.get('duration'):.2f}s, {md.get('integrated_lufs')} LUFS, "
                  f"TP {md.get('true_peak_dbtp')} dBFS, preto {md.get('black_seconds')}s, congelado {md.get('freeze_seconds')}s."]
        m += [f"Elementos originais: {', '.join(v['elementos_originais']) or 'nenhum'} | "
              f"Cosméticos (peso baixo): {', '.join(v['elementos_cosmeticos'])}", ""]
    (rd / "validacao_monetizacao.md").write_text("\n".join(m), encoding="utf-8")
