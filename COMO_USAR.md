# Como usar a Skill — YouTube Shorts Clip Editor

Guia prático, do zero ao primeiro lote de cortes. Há duas formas de usar: **conversando com o Claude**
(recomendado) ou **pelo terminal**, sem o Claude.

---

## 1. Instalar (uma vez só)

### 1.1 Instalar a Skill no Claude
1. Abra o Claude → **Configurações → Capacidades → Skills**.
2. Clique em **Enviar skill** e selecione o arquivo `youtube-shorts-clip-editor.skill`.
3. Confirme que ela aparece ativada na lista.

### 1.2 Instalar as ferramentas no computador onde os vídeos serão processados
A Skill usa Python e FFmpeg. Instale uma vez:

| Sistema | Comandos |
|---|---|
| macOS | `brew install ffmpeg python` |
| Ubuntu/Debian | `sudo apt install ffmpeg python3-pip` |
| Windows | Baixe o FFmpeg "full" em https://www.gyan.dev/ffmpeg/builds/, extraia e adicione a pasta `bin` ao PATH. Instale o Python em python.org (marque "Add to PATH"). |

Depois, dentro da pasta da Skill:
```bash
pip install -r requirements.txt
```

Teste rápido (os três nomes devem aparecer):
```bash
ffmpeg -hide_banner -filters | grep -E " ass | loudnorm | sidechaincompress "
```

> Na primeira transcrição, o Whisper baixa o modelo de voz (~500 MB no modelo `small`). Isso acontece
> só uma vez.

---

## 2. Usar conversando com o Claude (recomendado)

### 2.1 Envie o vídeo e peça os cortes
Anexe o vídeo (ou conecte a pasta onde ele está) e escreva algo como:

> "Faz 10 cortes para Shorts desse vídeo. O vídeo é do meu canal e eu apareço nele."

Outros exemplos que funcionam:
- "Transforma esse podcast em Shorts de 20 a 25 segundos."
- "Quero cortes dessa live para o YouTube Shorts, avalia o risco de monetização."
- "Tenho as legendas em SRT, usa elas em vez de transcrever." (anexe o .srt)

### 2.2 Diga que tipo de vídeo é (muda o jogo de câmera)
Coloque no prompt, com suas palavras. Exemplos:

> "É um **vídeo de reação**: o jogo é o conteúdo principal e eu apareço na câmera no **canto inferior direito**."
> "É **gameplay**, sem câmera." · "É um **podcast** com dois convidados." · "É uma **aula** com slides."

| Tipo | Como fica o Short |
|---|---|
| **Reação** | Tela dividida: conteúdo (jogo/vídeo) em cima, sua reação embaixo. O espaço se ajusta suavemente: mais reação quando você reage ou enfatiza algo, mais conteúdo quando você fica em silêncio. Nenhum texto fica sobre o seu rosto, e a câmera não aparece duplicada em cima. |
| **Gameplay / aula / tela** | Foco no conteúdo: mostra a maior parte da cena e acompanha a ação devagar. |
| **Podcast / entrevista / vlog** | Enquadramento no rosto de quem fala, com movimento suave. |
| **Não disse** | A Skill tenta descobrir sozinha (uma câmera parada num canto = reação). |

Dizer o canto da câmera ajuda quando a detecção automática falha (câmera muito pequena, sem moldura etc.).

Todos os modos usam transições suaves: fade na abertura, dissolve curto nos cortes, zooms lentos e cartões
que entram e saem com fade. Não há efeitos sonoros de transição.

### 2.3 Responda a pergunta sobre direitos
O Claude vai perguntar **de quem é o vídeo**. Essa resposta muda muito a avaliação:

| Resposta | Quando usar | Efeito na avaliação |
|---|---|---|
| **É meu e eu apareço** | Vídeo do seu canal, você fala/aparece | Melhor cenário possível |
| **É meu** | Vídeo do seu canal, mas você não aparece | Pede confirmação de autoria |
| **Tenho licença** | Autorização por escrito de uso comercial | No mínimo risco moderado + revisão humana |
| **É de terceiros** | Não é seu e não há licença | No mínimo alto risco |
| **Não sei** | — | Tratado como terceiros até você confirmar |

### 2.4 O que o Claude faz sozinho
1. Analisa o arquivo (duração, resolução, qualidade do áudio).
2. Transcreve todo o vídeo e entende os assuntos.
3. Escolhe os melhores momentos — **se não houver 10 bons, entrega menos e explica o motivo**.
4. Gera os elementos da edição **a partir do que é dito**: título no topo, contexto, números animados,
   contrastes ("não é X, é Y"), pontos-chave e frase de fechamento.
5. Revisa esses elementos, renderiza os vídeos, valida e corrige o que for possível.
6. Entrega os cortes e os relatórios.

### 2.5 Pedir ajustes depois
Você pode pedir mudanças em linguagem natural, por exemplo:
- "No corte 3, troca o título por 'Eu travei na frente de 20 pessoas'."
- "Tira a música do corte 5."
- "O corte 2 tá com estilo muito sério, deixa mais energético."
- "Refaz só os cortes 1 e 4."
- "Censura a palavra X no corte 6."
- "Deixa os cortes ainda mais calmos" / "um pouco mais dinâmico."
- "Dá mais espaço para a minha reação no corte 4."

O Claude ajusta e renderiza **só os cortes que mudaram**.

---

## 3. Usar pelo terminal (sem o Claude)

Substitua `S` pelo caminho da pasta `scripts` da Skill.

**Tudo automático, em um comando:**
```bash
python3 S/run_pipeline.py run --project meu_projeto --video podcast.mp4 --rights own --creator-on-screen --auto
# vídeo de reação com a câmera no canto inferior direito:
python3 S/run_pipeline.py run --project meu_react --video react.mp4 --rights own --creator-on-screen \
    --content-type reacao --facecam bottom-right --auto
```

**Passo a passo (permite revisar antes de renderizar):**
```bash
python3 S/run_pipeline.py init    --project meu_projeto --video podcast.mp4 --rights own --creator-on-screen
python3 S/run_pipeline.py analyze --project meu_projeto
python3 S/run_pipeline.py select  --project meu_projeto --auto
#   → revise meu_projeto/analise/editorial_auto.json; ajustes vão em meu_projeto/analise/editorial.json
python3 S/run_pipeline.py produce --project meu_projeto
```

| Opção | Para quê |
|---|---|
| `--rights own / licensed / third_party / unknown` | De quem é o vídeo |
| `--creator-on-screen` | Você aparece/fala no vídeo |
| `--rights-note "texto"` | Descrição da licença (quem autorizou, quando, escopo) |
| `--content-type reacao / gameplay / aula / podcast / entrevista / vlog / auto` | Tipo de vídeo (define a câmera) |
| `--facecam top-left / top-right / bottom-left / bottom-right / auto` | Canto da câmera, em vídeos de reação |
| `--transcript legendas.srt` | Usar legenda pronta (SRT/VTT) em vez do Whisper |
| `--only 02,05` | Agir só nesses cortes |
| `--force` | Refazer mesmo que já esteja pronto |
| `--config meu.json` | Mudar configurações (ver seção 6) |

Outros comandos:
```bash
python3 S/run_pipeline.py status   --project meu_projeto          # ver em que etapa está
python3 S/run_pipeline.py render   --project meu_projeto --only 03 --force
python3 S/validate.py              --project meu_projeto          # revalidar sem renderizar
python3 S/run_pipeline.py reset    --project meu_projeto --from select
```

**Se o processo parar no meio** (queda de energia, Ctrl+C): rode o mesmo comando de novo. Ele continua de
onde parou, sem refazer o que já terminou.

---

## 4. O que você recebe

```
meu_projeto/
  cortes/        corte_01.mp4 … corte_10.mp4   ← prontos para subir no YouTube Shorts
  relatorios/    relatorio_geral.md            ← resumo, tabela e detalhes de cada corte
                 validacao_monetizacao.md      ← avaliação de risco de cada corte
                 timestamps.json               ← de onde cada corte saiu no vídeo original
                 score_cortes.json             ← pontuações
  transcricao/   transcript.txt / .srt         ← transcrição completa do vídeo
  analise/       editorial_auto.json           ← elementos gerados (títulos, contexto, gráficos…)
  logs/          pipeline.log                  ← log detalhado, se algo der errado
```

Formato dos cortes: MP4 · H.264 · AAC · 1080x1920 (9:16) · 20 a 25 s · volume em -14 LUFS (padrão do
YouTube) · sem marca d'água.

---

## 5. Como ler a avaliação de monetização

Cada corte recebe uma classificação:

| Classificação | Significado | O que fazer |
|---|---|---|
| **BAIXO RISCO** | Nenhum problema relevante nos critérios verificados | Pode publicar (revise título/descrição) |
| **RISCO MODERADO** | Pontos de atenção ou itens que exigem um olhar humano | Leia "Exige revisão humana" e confira |
| **ALTO RISCO** | Problemas que podem comprometer a monetização | Corrija antes de publicar |
| **NÃO RECOMENDADO** | Problemas graves | Não publique na forma atual |

No `validacao_monetizacao.md`, cada corte tem 8 itens: classificação, pontuação, problemas, motivos,
recomendações, melhorias de originalidade, riscos de direitos autorais e **o que exige revisão humana**.
Comece sempre por este último.

**Importante:**
- A pontuação 0–100 é só para organização interna. **Não é nota do YouTube** nem chance de aprovação.
- Nenhuma ferramenta garante monetização. Quem decide é o YouTube, que avalia o canal como um todo.
- A ferramenta não consegue detectar Content ID. Se houver música de fundo no vídeo original, ouça antes
  de publicar.

---

## 6. Configurações úteis

Crie um arquivo `meu.json` só com o que quiser mudar e use `--config meu.json`:

```json
{
  "clips": {"count": 5, "min_duration": 20, "max_duration": 25},
  "transcription": {"model": "large-v3", "language": "pt"},
  "audio": {"music_enabled": false, "music_level_db": -28},
  "jump_cuts": {"enabled": true, "min_pause_seconds": 0.65},
  "transitions": {"cut_dissolve": 0.18, "intro_fade": 0.5},
  "camera": {"split_content": 0.58, "split_min": 0.5, "split_max": 0.64, "punch_amount": 0.045}
}
```

| Configuração | Efeito |
|---|---|
| `clips.count` | Quantos cortes no máximo |
| `transcription.model` | `small` (padrão, bom em CPU), `medium`, `large-v3` (melhor, ideal com GPU) |
| `transcription.language` | Força o idioma (`"pt"`, `"en"`…); `null` = detectar sozinho |
| `audio.music_enabled` | Liga/desliga a trilha de fundo |
| `audio.music_level_db` | Volume da trilha (mais negativo = mais baixo) |
| `jump_cuts.min_pause_seconds` | Só pausas maiores que isso são cortadas (maior = cortes mais raros e naturais) |
| `transitions.cut_dissolve` | Duração do dissolve em cada corte, em segundos |
| `camera.split_content` | Em reação: fração da tela para o conteúdo (0,58 = 58% conteúdo, 42% reação) |
| `camera.split_min` / `split_max` | Limites da variação automática conteúdo ↔ reação |
| `camera.punch_amount` | Intensidade dos zooms de ênfase (0 = sem zoom) |

Músicas: por padrão a trilha é **gerada pela própria ferramenta** (sem direitos de terceiros). Para usar
suas músicas, veja a seção 5 do `README.md`.

---

## 7. Problemas comuns

| Problema | Solução |
|---|---|
| "ffmpeg não encontrado" | Instale o FFmpeg (seção 1.2) e reabra o terminal |
| O Whisper não baixa o modelo | Verifique a internet ou use `--transcript` com um SRT/VTT |
| Saíram menos de 10 cortes | O vídeo não tinha 10 momentos bons e independentes; isso é intencional |
| Legenda fora de sincronia | Acontece com SRT importado; prefira a transcrição pelo Whisper |
| Título ou texto estranho num corte | Peça ao Claude para ajustar, ou edite `analise/editorial.json` e rode `produce` |
| Rosto não fica centralizado | Rostos de perfil ou pequenos podem falhar; a ferramenta usa o layout de conteúdo |
| Vídeo de reação saiu sem tela dividida | A câmera não foi achada: diga o canto dela no prompt ou use `--facecam` |
| Cortes ainda parecem rápidos | Aumente `jump_cuts.min_pause_seconds` (ex.: 0.9) ou desligue com `"enabled": false` |
| Demora muito | ~30 s por corte em CPU comum; vídeos longos levam mais na transcrição (use GPU se tiver) |

Para detalhes técnicos (arquitetura, módulos, limitações), veja o `README.md`.
