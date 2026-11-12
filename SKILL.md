---
name: youtube-shorts-clip-editor
description: Transforma um vídeo longo (MP4/MOV/MKV/WEBM, qualquer duração) em até 10 YouTube Shorts de 20–25 s em 1080x1920. Transcreve o vídeo, entende o conteúdo, seleciona os melhores momentos e monta a edição a partir da transcrição (títulos, contexto, números animados, contrastes, pontos-chave), com câmera e transições suaves (tela dividida conteúdo + reação para vídeos de reação, prioridade ao conteúdo nos demais), rastreamento de rosto e da ação, dissolves, legendas animadas palavra-a-palavra, trilha própria com ducking e loudness -14 LUFS (sem narração), mais um validador de risco de monetização (conteúdo reutilizado/inautêntico, direitos autorais, adequação a anunciantes, qualidade) com autocorreção e relatórios. Use sempre que o usuário quiser cortes, clipes, Shorts, Reels ou TikToks a partir de um vídeo, podcast, live, aula ou entrevista, ou quiser saber se cortes podem ser monetizados no YouTube — mesmo que não diga "Shorts".
---

# AI YouTube Clip Editor & Monetization Validator

O vídeo é entendido pela transcrição, e a edição é construída a partir dela: os scripts transcrevem,
analisam, geram os elementos visuais a partir do que é dito (título, contexto, números animados, contrastes,
pontos-chave, fechamento), renderizam e medem. **Você faz o trabalho de editor**: entende o vídeo, escolhe os
momentos e revisa esses elementos. Não há narração. O validador penaliza edição só cosmética (zoom,
legenda, música); não tente "enganá-lo", melhore o corte.

Caminhos abaixo são relativos à pasta desta skill (`SKILL_DIR`). Leia `references/editorial_guide.md`
antes do passo 4 e `references/youtube_policies.md` se o usuário perguntar sobre políticas.
Se o usuário perguntar como usar, instalar ou configurar a Skill, responda com base em `COMO_USAR.md`.

## Passo 0 — Ambiente (uma vez por sessão)

```bash
python3 -c "import numpy, cv2, PIL, faster_whisper" && ffmpeg -hide_banner -filters | grep -E " ass | loudnorm "
```
Se faltar algo: `pip install -r SKILL_DIR/requirements.txt` (use `--break-system-packages` se o pip pedir) e
instale um FFmpeg com libass. Na primeira transcrição o Whisper baixa o modelo do Hugging Face; se a rede
bloquear, peça ao usuário um SRT/VTT (ex.: legendas do YouTube) e use `--transcript`.

## Passo 1 — Direitos (pergunte se não souber)

O validador precisa saber de quem é o vídeo. Pergunte uma vez, objetivamente:
- `own` — o vídeo é do próprio canal (adicione `--creator-on-screen` se o dono aparece/fala nele);
- `licensed` — há licença escrita de uso comercial (registre em `--rights-note`);
- `third_party` — é de terceiros sem licença; `unknown` — não sabe.
Seja franco: com `third_party`/`unknown` o resultado será, no máximo, risco alto/moderado, por mais que a
edição seja boa — a política de conteúdo reutilizado vale **mesmo com permissão**, e direitos são pré-requisito.

## Passo 2 — Inicializar e analisar

```bash
python3 SKILL_DIR/scripts/run_pipeline.py init --project PROJ --video VIDEO --rights own --creator-on-screen
python3 SKILL_DIR/scripts/run_pipeline.py analyze --project PROJ [--transcript legendas.srt]
```
Gera `PROJ/transcricao/` e `PROJ/analise/candidates.md` (candidatos de 20–25 s já descontando jump cuts,
com scores heurísticos, assuntos, alertas de palavrão/tema sensível). Vídeos longos são processados em blocos
de 10 min com retomada: se cair, rode o mesmo comando de novo.

## Passo 3 — Entender o vídeo e selecionar

Leia `transcricao/transcript.txt` para entender do que o vídeo trata (assuntos, tese, histórias, números
citados) e depois `candidates.md`. Escolha até 10 cortes sem sobreposição, cada um com gancho forte na 1ª
frase, começo-meio-fim e sentido sem o resto do vídeo. Os scores são pista, não veredito. **Se não houver 10
bons, escolha menos e explique** — nunca complete a cota com cortes fracos. Escreva
`PROJ/analise/selection.json` (formato no guia editorial) e rode:

```bash
python3 SKILL_DIR/scripts/run_pipeline.py select --project PROJ
```
Isso valida a seleção e **gera os elementos de edição a partir da transcrição** em
`PROJ/analise/editorial_auto.json`: título-gancho (a frase mais forte do trecho), contexto (o que foi dito
antes do trecho no vídeo), números falados → contador animado no momento da fala, "não é X, é Y" → cartão
de contraste, pontos-chave em lista, e fechamento com a conclusão do trecho.

## Passo 4 — Revisar os elementos (baseados só na transcrição)

Abra `editorial_auto.json` e revise corte a corte com o entendimento que você tem do vídeo. Copie para
`PROJ/analise/editorial.json` apenas os cortes que quiser ajustar (o que estiver lá substitui o automático):
corrigir um título truncado ou fraco, trocar o rótulo do assunto, escolher o número certo para o contador,
reescrever o fechamento, ajustar `style`. Mantenha tudo fiel à fala: o conteúdo dos elementos vem do que é
dito no vídeo — não invente dados, não atribua ao autor algo que ele não disse, não acrescente narração.
Se a transcrição for confiável e os elementos estiverem bons, pode seguir sem `editorial.json`.

## Passo 5 — Produzir, validar, corrigir

```bash
python3 SKILL_DIR/scripts/run_pipeline.py produce --project PROJ
```
Seleção validada → plano por corte → render (≈25–30 s de processamento por corte em CPU de 2 núcleos) →
validador com medições reais (codec, resolução, duração, LUFS, true peak, telas pretas, congelamentos,
legendas, palavrões, temas sensíveis, licenças, uniformidade do lote) → autocorreção do que for mecânico
(loudness, duração, palavrão em título, bipe em palavrão frequente, variação de template) → re-render →
revalidação → relatórios. Problemas **não mecânicos** (poucos elementos, direitos) não são
"corrigidos" pelo script: cabe a você melhorar o `editorial.json` e rodar `produce` de novo (só os cortes
cujo plano mudou são re-renderizados), ou levar a decisão ao usuário.

Antes de entregar, confira visualmente 2–3 quadros de alguns cortes:
`ffmpeg -ss 8 -i PROJ/cortes/corte_01.mp4 -frames:v 1 /tmp/f.png` e abra a imagem.

## Passo 6 — Entregar

Envie os MP4 de `PROJ/cortes/` e os relatórios de `PROJ/relatorios/` (`relatorio_geral.md`,
`validacao_monetizacao.md`, `timestamps.json`, `score_cortes.json`). No resumo: quantos cortes saíram (e por
que menos de 10, se for o caso), a tabela de risco e os itens de revisão humana. **Nunca diga que um corte
"será aprovado" ou "é seguro para monetizar"**: a classificação é técnica e preventiva; a decisão é do
YouTube, considera o canal inteiro e direitos que a ferramenta não verifica. A pontuação 0–100 é interna.

## Atalhos e comandos úteis

- Tudo automático (seleção heurística + elementos gerados da transcrição, sem revisão):
  `run_pipeline.py run --project PROJ --video V --rights own --creator-on-screen --auto`
- Refazer só alguns: `run_pipeline.py render --project PROJ --only 02,05 --force`
- Revalidar sem renderizar: `python3 SKILL_DIR/scripts/validate.py --project PROJ`
- Estado/retomada: `run_pipeline.py status --project PROJ`; `reset --from select`
- Overrides de config: `--config meu.json` (ver `config/default.json`: modelo Whisper, limiares, LUFS, etc.)
- Logs detalhados: `PROJ/logs/pipeline.log`

## Problemas comuns

- Whisper não baixa o modelo → `--transcript` com SRT/VTT, ou `transcription.model` apontando para pasta local.
- Poucos candidatos → vídeo curto ou fala sem pontuação; selecione menos cortes e informe.
- Rosto não detectado → layout `content` (conteúdo em destaque sobre fundo desfocado); force com
  `"layout": "face"` no editorial se souber que há um rosto.
- Reação sem facecam encontrada (log avisa) → refaça com `--facecam <canto>`; com a dica, a câmera é
  localizada pelo canto mesmo sem detectar o rosto.
- O usuário achou rápido/seco demais → aumente `jump_cuts.min_pause_seconds` (menos cortes) ou
  `transitions.cut_dissolve`; pediu mais dinâmico → aumente `camera.punch_amount` com moderação.
