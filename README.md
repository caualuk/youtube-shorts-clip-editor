# AI YouTube Clip Editor & Monetization Validator

> **Primeira vez?** Comece pelo [COMO_USAR.md](COMO_USAR.md) — guia passo a passo de instalação e uso.

Skill para Claude + ferramenta de linha de comando que transforma um vídeo longo em até 10 YouTube Shorts
(20–25 s, 1080x1920, H.264/AAC) com edição autoral e avaliação de risco de monetização.

## 1. Instalação

**Requisitos:** Python 3.10+, FFmpeg 6+ com `libass` e `libx264` (builds oficiais de ffmpeg.org/gyan.dev/
BtbN já incluem), ~2 GB livres por hora de vídeo para temporários.

```bash
# macOS
brew install ffmpeg python
# Ubuntu/Debian
sudo apt install ffmpeg python3-pip
# Windows: baixe o build "full" em https://www.gyan.dev/ffmpeg/builds/ e coloque ffmpeg/ffprobe no PATH

pip install -r requirements.txt
```

Checagem: `ffmpeg -hide_banner -filters | grep -E " ass | loudnorm | sidechaincompress "` deve listar os três.

**Modelo de transcrição:** faster-whisper baixa o modelo na primeira execução (Hugging Face). Padrão
`small` (bom equilíbrio em CPU). Em GPU NVIDIA use `large-v3` (`transcription.model`). Sem internet:
baixe o modelo CTranslate2 em outra máquina e aponte `transcription.model` para a pasta.

**Como Skill do Claude:** envie o `.zip`/`.skill` em Configurações → Capacidades/Skills. O Claude passa a
conduzir o fluxo e escrever a parte editorial.

## 2. Uso

```bash
S=caminho/para/youtube-shorts-clip-editor/scripts
python3 $S/run_pipeline.py init    --project meu_proj --video podcast.mp4 --rights own --creator-on-screen
python3 $S/run_pipeline.py analyze --project meu_proj          # probe + transcrição + candidatos
# escreva (ou deixe o Claude escrever) meu_proj/analise/selection.json — ou use --auto
python3 $S/run_pipeline.py select  --project meu_proj          # valida e gera editorial_auto.json da transcrição
# opcional: ajuste cortes específicos em meu_proj/analise/editorial.json
python3 $S/run_pipeline.py produce --project meu_proj          # render + validação + relatórios
```
Modo 100% automático: `run --project P --video V --rights own --creator-on-screen --auto` (seleção heurística
e elementos gerados da transcrição, sem revisão). Não há narração em nenhum modo: a edição é construída a
partir do que é dito no vídeo.

Seleção via API (lotes sem conversa): `"llm": {"enabled": true}` no config, `ANTHROPIC_API_KEY` no ambiente e
`--llm`. O gasto estimado é acumulado em `state.json` e bloqueado acima de `llm.max_budget_usd`.

## 3. Estrutura do projeto gerado

```
meu_proj/
  video_original/   (link simbólico; --copy-video para copiar)
  transcricao/      transcript.json (palavras com tempo), .srt, .txt, chunks/ (retomada)
  analise/          video_info.json, analysis.json, candidates.md, selection.json, editorial.json,
                    selected_clips.json, plans/corte_XX.json
  cortes/           corte_01.mp4 … corte_10.mp4
  relatorios/       relatorio_geral.md, validacao_monetizacao.md, timestamps.json, score_cortes.json,
                    validacao.json
  logs/pipeline.log   state.json   rights.json
```

## 4. Arquitetura

| Módulo (`scripts/sce/`) | Responsabilidade |
|---|---|
| `core.py` | config (JSON com merge), logs, checkpoint/retomada (`state.json`, escrita atômica), FFmpeg |
| `probe.py` | duração, resolução, FPS, rotação, proporção, nível/pico/ruído do áudio |
| `transcribe.py` | blocos de 10 min com sobreposição e deduplicação; faster-whisper/openai-whisper; import SRT/VTT/JSON |
| `analyze.py` | frases, energia de áudio em streaming, cenas, assuntos (coesão lexical), candidatos 20–25 s, 9 scores |
| `faces.py` | detecção de rosto (Haar + CLAHE; YuNet opcional), localização da facecam e da sua moldura, rastreador de ação do conteúdo |
| `selection.py` | valida a seleção editorial; modo auto por programação dinâmica (máx. cortes sem sobreposição) |
| `auto_editorial.py` | elementos de edição a partir da transcrição: gancho, contexto do vídeo, números → contador, "não é X, é Y" → contraste, pontos-chave, fechamento |
| `plan.py` | estratégia por corte: jump cuts, layout, câmera, legendas, estilos, variações anti-template |
| `graphics.py` | cartões, título, gráficos animados (lista/contador/comparação), barra de progresso |
| `captions.py` | ASS palavra-a-palavra, destaque, animação de entrada, quebra de linha |
| `audio.py` | emendas com fades de 30 ms, fade de entrada/saída, EQ/compressão/denoise, trilha gerada com ducking, bipe, loudnorm 2 passagens (sem efeitos sonoros de transição) |
| `render.py` | "diretor" de câmera (layouts reação/conteúdo/rosto), dissolve nos cortes, fades, composição quadro a quadro → FFmpeg (libass + x264) |
| `validator.py` | Monetization Compliance Validator (independente; também via `validate.py`) |
| `report.py` | relatórios |
| `llm.py` | API opcional com orçamento |

Por que estas tecnologias: **FFmpeg** é o padrão de facto, rápido e estável para decodificar/codificar e
medir loudness; **faster-whisper** tem a mesma precisão do Whisper com ~4x a velocidade e menos memória, e
dá timestamps por palavra; **OpenCV/numpy** para composição quadro a quadro (controle total de câmera e
gráficos com memória constante); **libass** para legendas animadas de qualidade tipográfica. **MoviePy não
foi usado**: para 1080x1920 ele é mais lento, consome mais memória e esconde o controle fino de filtros —
sem vantagem aqui.

## 5. Câmera e transições

- **Tipo de vídeo** (`--content-type`): `reacao` (tela dividida conteúdo + reação), `gameplay`/`aula`
  (prioridade ao conteúdo), `podcast`/`entrevista`/`vlog` (rosto) ou `auto` (detecta). Em reação, `--facecam`
  indica o canto da câmera se a detecção falhar.
- **Reação:** a facecam é localizada como um rosto parado no mesmo lugar, e a moldura dela pelas bordas retas
  que não mudam entre quadros. O painel de cima mostra o conteúdo seguindo a ação, sem a facecam duplicada
  (a área é reconstruída com o entorno). O de baixo mostra só a câmera. O espaço entre os dois varia de 50% a
  64% de conteúdo, planejado antes de renderizar: mais reação nas ênfases da fala, mais conteúdo nos silêncios,
  com no máximo 12% da tela por segundo de variação. Textos e cartões nunca ficam sobre o rosto.
- **Sem reação:** prioridade ao conteúdo. Mostra cerca de 75% da cena, seguindo a ação com zona morta e
  movimento lento.
- **Transições:** fade de abertura (0,5 s), dissolve de 0,18 s em cada jump cut, zooms de ênfase com entrada de
  0,5 s e saída de 0,7 s, cartões com fade e deslize, legendas com fade. Jump cuts só em pausas de 0,65 s ou
  mais, mantendo 0,25 s de respiro. Sem efeitos sonoros de transição.
- **Medição no teste:** a maior variação de imagem entre dois quadros caiu de 26,9 para 6,8 (conteúdo) e 6,3
  (reação, depois da abertura), com zero quadros bruscos.
- Ajustes: `transitions.*` e `camera.*` em `config/default.json`.

## 6. Músicas e B-roll

- Padrão: trilha e efeitos **sintetizados pelo próprio script** (obra gerada localmente, sem terceiros).
- Biblioteca própria: `"audio": {"music_source": "library", "music_library_dir": "/minhas_musicas"}`,
  `music_file` no editorial e um `licenses.json` na pasta (ver `assets/music/licenses.example.json`). Sem
  entrada com `"commercial_youtube": true`, o validador acusa risco e volta para a trilha gerada.
- B-roll/imagens: só com licença registrada no campo `license` do item.

## 7. Limitações conhecidas

- **A classificação não prevê a decisão do YouTube.** Não há API pública para isso; as regras são uma leitura
  das políticas (ver `references/youtube_policies.md`) e podem divergir da revisão real.
- Detecção de Content ID não é possível localmente. O validador só sinaliza **suspeita** de música de fundo
  (nível alto nas pausas da fala) e exige revisão humana.
- Léxicos de palavrões e temas sensíveis são heurísticos (PT-BR/EN) e não entendem contexto.
- Detector Haar incluso é frontal: perfis e rostos pequenos podem falhar (cai para layout `blurfill`).
  Para melhor rastreamento, adicione o modelo YuNet do opencv_zoo em `assets/models/`.
- Seleção e editorial de qualidade dependem do Claude (ou de você). O modo `--auto` é heurístico.
- Transcrição importada sem tempos por palavra → sincronia estimada (o validador avisa).
- Renderização em CPU: ~1,2x o tempo real por corte em 2 núcleos (25 s de Short ≈ 30 s de processamento).
- Cortes com mais de um orador na tela: o rastreador segue o maior rosto; não há troca por orador ativo.
- Reação: a detecção de facecam supõe uma câmera fixa durante o trecho. Câmera que muda de lugar, ou pessoa
  recortada por chroma key sem moldura, pode exigir `--facecam`. A área atrás da facecam no painel de conteúdo
  é reconstruída e pode borrar o que estiver exatamente ali.
- A divisão conteúdo/reação segue a fala de quem reage (ênfases e silêncios). Ela não "entende" o que acontece
  no jogo além da quantidade de movimento.
- O Whisper real não pôde rodar no ambiente de desenvolvimento (download do modelo bloqueado). A lógica de
  blocos, deduplicação na sobreposição e retomada foi testada com um motor simulado (546/546 palavras, 0
  duplicatas); a chamada ao faster-whisper segue a API documentada da biblioteca.
- O modo LLM via API foi implementado mas, no ambiente de desenvolvimento, não havia chave para teste
  ponta a ponta; o restante do pipeline foi testado (ver seção 7).

## 8. Exemplo de execução (teste real)

Vídeo de teste: 3min40, 1920x1080@30, fala em PT-BR, legenda SRT importada (sem Whisper, ver limitações).
Comando: `run --project projF --video entrevista_teste.mp4 --transcript legendas.srt --rights own --creator-on-screen --auto`

```
INFO Vídeo: 220.7s 1920x1080 @30.00fps
INFO 41 frases, 3 assuntos detectados.   34 candidatos gerados
WARN Apenas 7 momentos atingiram o limiar de qualidade … A Skill não completou a cota com cortes fracos.
INFO Elementos de edição gerados da transcrição → analise/editorial_auto.json
INFO Corte 02 renderizado (24.7s, 741 quadros, 32s de processamento).   … (7 cortes)
INFO Corte 02: RISCO MODERADO (interno 88/100)   # revisão humana: sincronia de legenda estimada
```
Elementos gerados da fala no corte 02 ("dinheiro"): título "As pessoas acham que precisam ganhar mais para
começar a investir"; contador animado **R$ 100.000 — com juros compostos** entrando no instante em que o número
é dito; cartão de contraste "O problema: ✕ o valor / ✓ o hábito"; fechamento "O problema: o hábito".
#   y o u t u b e - s h o r t s - c l i p - e d i t o r  
 