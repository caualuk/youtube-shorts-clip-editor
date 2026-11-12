# Guia editorial: selection.json e editorial.json

É aqui que entra o valor agregado real. Os scripts fazem a parte mecânica; a seleção e a contribuição
editorial são decisões de editor.

## selection.json (`<projeto>/analise/selection.json`)

```json
{"clips": [
  {"s0": 0, "s1": 3, "title": "As 2 horas que você perde todo dia",
   "justification": "Abre com pergunta direta, entrega método concreto e resultado mensurável."}
]}
```
- `s0`/`s1` = índices de sentença (veja `candidates.md` ou `sentences_words.json`). Também aceita
  `start`/`end` em segundos (são ajustados à fronteira de frase mais próxima).
- O script reajusta as bordas para caber em 20–25 s **após** os jump cuts e rejeita sobreposições.
- Critérios: gancho na 1ª frase, começo/meio/fim, independência de contexto, nada repetido entre
  cortes, potencial de retenção/comentário. Se não houver 10 bons, selecione menos e diga por quê.

## editorial_auto.json e editorial.json

Depois do `select`, o script grava `analise/editorial_auto.json` com os elementos gerados **a partir da
transcrição**. Para ajustar um corte, copie o bloco dele para `analise/editorial.json` (mesma estrutura) e
edite; o que estiver em `editorial.json` substitui o automático daquele corte. Sem narração: toda a edição se
apoia no que é dito no vídeo.

Chaves por ID do corte (`"01"`, `"02"`, … na ordem cronológica da seleção):

```json
{"clips": {"02": {
  "style": "educativo",
  "hook_title": "Você não precisa ganhar mais para investir",
  "topic_label": "Dinheiro",
  "keywords": ["investir", "mil", "hábito"],
  "context_card": {"label": "Antes deste trecho", "text": "“Agora vamos falar de dinheiro.”"},
  "explainers": [
    {"kind": "stat", "title": "Em números", "value": 100000, "prefix": "R$ ",
     "caption": "com juros compostos", "at_src": 47.4},
    {"kind": "contrast", "title": "O problema: não é isso…", "items": ["o valor", "o hábito"], "at_src": 49.8}
  ],
  "closing_text": "O problema: o hábito",
  "facts": [],
  "broll": [], "music_mood": "educativo", "censor_words": [], "layout": "blurfill"
}}}
```

| Campo | Uso | Origem típica |
|---|---|---|
| `style` | energetico, educativo, historia, debate, humor | escolhido pelo tom da fala |
| `hook_title` | gancho no topo (0–3,2 s), até ~12 palavras | frase mais forte do trecho |
| `topic_label` | selo do assunto, 1 palavra | assunto citado no vídeo |
| `context_card` | o que o espectador do Short não viu | frase dita **antes** do trecho |
| `explainers[]` | `stat` (value/prefix/suffix/caption), `contrast` (items: [errado, certo]), `list` (items), `compare` (items+values) | números, contrastes e passos falados no trecho |
| `at_src` / `at` | momento de entrada: `at_src` = segundo no vídeo original (sincroniza com a fala); `at` = segundo no corte | — |
| `closing_text` | conclusão do trecho nos últimos 2,4 s | conclusão dita no trecho, ou pergunta se for opinião |
| `facts[]` | opcional: dado externo verificável **com fonte** | só se o usuário quiser complementar |
| `broll[]` | vídeo/imagem licenciado (`mode`: full ou pip) | exige `license` |

## Regras de integridade

1. **Fidelidade à fala.** Todo texto deve corresponder ao que é dito no vídeo (pode ser encurtado, não
   distorcido). Encurtar "A verdade é que adultos aprendem de forma diferente" para "Adultos aprendem de
   forma diferente" é ok; mudar o sentido não é.
2. **Não atribua falas ao autor.** Citação entre aspas só para frase dita de fato (o contexto automático usa
   aspas porque é literal).
3. **Números certos.** Confira se o contador mostra o número que importa e com a unidade correta.
4. **Sem narração.** O projeto não usa voz adicional; o valor vem da seleção, da contextualização e da
   visualização do que é dito.
5. Textos curtos: o Short tem 20–25 s.
