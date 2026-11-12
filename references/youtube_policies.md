# Políticas do YouTube aplicáveis (verificadas em 11/11/2026)

Resumo em linguagem própria das páginas oficiais, para orientar o validador. **Revise as fontes
antes de cada uso em produção — as políticas mudam.** Isto não é aconselhamento jurídico.

## 1. Políticas de monetização do YPP — support.google.com/youtube/answer/1311392

- Conteúdo monetizado deve ser **original e autêntico**.
- **Conteúdo inautêntico** (nome dado em 15/07/2025 ao antigo "conteúdo repetitivo"): material genérico,
  produzido em massa ou com cara de template, com variação mínima entre vídeos do canal. Vinhetas iguais
  são aceitas se o conteúdo principal muda. Conteúdo de IA baseado em template genérico sem visão do
  criador entra aqui.
- **Conteúdo reutilizado**: material que já existe online ou em outra fonte, republicado **sem comentário
  original significativo, modificação substancial ou valor educativo/de entretenimento agregado**.
  - Permitido (exemplos oficiais): clipes em crítica, cenas com nova narração, replays com explicação do
    criador, reacts com comentário, filmagem editada com nova narrativa e comentário, conteúdo que mostra
    principalmente o próprio uploader, material reutilizado em que o criador aparece ou explica a sua
    contribuição, filmagem fortemente editada com efeitos que a tornam distinta.
  - Não permitido: clipes de programas juntados com pouca ou nenhuma narrativa, compilações de outras
    redes, conteúdo baixado/copiado sem mudanças substanciais, vídeos guiados por reações não verbais
    sem comentário em voz, leitura em voz alta de texto alheio.
  - **Vale mesmo com permissão do autor original** e é independente de direitos autorais.
  - A revisão olha vídeos, título, descrição e descrição do canal; a sanção pode atingir o **canal inteiro**.

## 2. O que pode ser monetizado — support.google.com/youtube/answer/2490020

- É preciso ter direitos de **uso comercial** de todos os elementos de áudio e vídeo.
- Conteúdo achado online pertence ao criador original; licença comercial é necessária.
- Música: gravações comerciais (inclusive instrumentais e ao vivo) não são elegíveis sem licença; a
  Biblioteca de Áudio do YouTube e o Creator Music são aceitos. Creative Commons/royalty-free exige
  licença comercial (e às vezes crédito). Fair use é defesa, não garantia.

## 3. Diretrizes da comunidade — support.google.com/youtube/answer/9288567

A URL fornecida aponta para as **Diretrizes da Comunidade** (spam, conteúdo sensível, violento, bens
regulados, desinformação; contexto EDSA pode permitir exceções). As regras de **adequação a
anunciantes** ficam em support.google.com/youtube/answer/6162278. Pontos usados aqui:
- Desde 07/2025, palavrão forte nos primeiros 7 s pode receber receita plena; **uso frequente** de
  palavrões fortes continua violando, e palavrão em **título/miniatura** continua limitando anúncios.
- Violência gráfica, conteúdo sexual, drogas, tragédias e temas controversos podem limitar anúncios
  conforme o grau de detalhe e o contexto.

## 4. Monetização de Shorts — support.google.com/youtube/answer/12504220

- Exige participação no YPP e aceite do Módulo de Monetização de Shorts.
- **Inelegíveis à divisão de receita:** Shorts não originais — clipes sem edição de filmes/TV,
  reuploads de conteúdo alheio, compilações sem conteúdo original —, visualizações artificiais e
  conteúdo fora das diretrizes para anunciantes.
- Música de parceiros reduz a fatia do Creator Pool (trilha gerada localmente não é "música de
  parceiros").
- A página anuncia mudanças a partir de 01/02/2027 (limiar de 10 milhões de visualizações qualificadas
  de Shorts em 90 dias para a receita mensal de anúncios/Premium de Shorts). Confirme antes de planejar.

## Como o validador traduz isso

| Política | Regra no validador |
|---|---|
| Reutilizado exige transformação real | Zoom/legenda/música/transição = "cosmético", peso ~2/30; elementos derivados da fala valem menos que contexto e gráficos explicativos |
| Permissão não isenta | `licensed` com menos de 3 elementos de edição → mínimo ALTO RISCO; com 3+ → mínimo MODERADO + revisão humana |
| Conteúdo do próprio uploader é permitido | `own` + `--creator-on-screen` → eixo B máximo |
| Direitos não verificáveis | `unknown` → nunca BAIXO RISCO; tratado como terceiros |
| Inautêntico/produção em massa | Similaridade de template entre cortes ≥ 70% → alerta + autocorreção de variação |
| Título com palavrão | crítico → mascaramento automático |
| Palavrão frequente | alto → bipe/mudo automático das palavras |
| Música/B-roll | exige licença registrada; trilha gerada localmente = sem terceiros |
