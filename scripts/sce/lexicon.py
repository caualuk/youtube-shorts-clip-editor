"""Léxicos PT-BR/EN usados na análise e no validador.

São listas heurísticas para TRIAGEM — não substituem a revisão humana nem a análise do YouTube.
Edite à vontade; o validador lê daqui.
"""
import re
import unicodedata


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", norm(text))


STOPWORDS = set("""
a o as os um uma uns umas de da do das dos em na no nas nos por para pra pro com sem sob sobre entre ate
e ou mas que se como quando onde porque pois entao tambem ja nao sim muito muita muitos muitas mais menos
eu tu ele ela nos vos eles elas voce voces me te lhe nos lhes meu minha seu sua nosso nossa isso isto aquilo
esse essa este esta aquele aquela ai la aqui ali foi era ser estar ter tem tinha vai vou vamos fazer faz
ta tava to ne tipo coisa coisas gente cara entao assim agora so bem bom boa la
the a an of to in on for with without and or but that this these those is are was were be been being it its
i you he she we they me him her us them my your his our their so just like really very do does did have has
""".split())

# Conectores que indicam que a frase depende de contexto anterior
DEPENDENT_STARTS = set(norm(w) for w in """
e mas entao porque pois porem contudo todavia alias inclusive tambem isso esse essa ele ela eles elas
daí dai ai depois senao tipo ou seja and but so because then also that this he she they it which
""".split())

HOOK_MARKERS = [norm(w) for w in """
segredo ninguem nunca jamais sempre erro erros verdade mentira descobri incrivel surpreendente maior pior melhor
proibido cuidado atencao importante porque como por que qual quanto dica truque metodo regra problema solucao
voce sabia imagine pare chocante absurdo louco errado certo diferenca motivo motivos
secret never always mistake truth lie discovered incredible biggest worst best why how stop imagine wrong
""".split()]

OPINION_MARKERS = [norm(w) for w in """
acho acredito discordo concordo opiniao polemico polemica injusto absurdo ridiculo deveria deveriam
odeio amo prefiro sinceramente honestamente na verdade think believe disagree agree opinion should hate love honestly
""".split()]

HUMOR_MARKERS = [norm(w) for w in "kkk kkkk haha hahaha rsrs risos piada engracado zoeira laughs lol funny".split()]

EMPHASIS_WORDS = set(HOOK_MARKERS + [norm(w) for w in """
nunca sempre todo todos nada zero milhao milhoes bilhao mil dobro metade primeiro ultimo unico unica
dinheiro tempo vida saude medo sucesso fracasso
""".split()])

# Palavrões — "strong" vs "moderate" segue a lógica das diretrizes para anunciantes
# (uso esporádico de moderados costuma ser ok; uso frequente de fortes limita anúncios).
PROFANITY_STRONG = set(norm(w) for w in """
porra caralho foda foder fodase fodido fudido buceta cu arrombado viado puta puto putaria
filhodaputa fdp vsf pqp cacete merda
fuck fucking fucker motherfucker shit bitch cunt dick pussy asshole
""".split())
PROFANITY_MODERATE = set(norm(w) for w in """
droga diabos inferno bosta saco idiota imbecil otario babaca besta trouxa desgraca desgracado
damn hell crap bastard idiot stupid ass
""".split())

# Temas sensíveis para adequação a anunciantes (exigem revisão humana de contexto, não reprovação automática)
SENSITIVE_TOPICS = {
    "violencia": "matar assassinato assassinou morte morreu tiro tiroteio facada sangue espancou agressao estupro "
                 "tortura guerra bomba atentado terrorismo kill murder shooting stabbing blood war bomb terror",
    "drogas": "cocaina maconha crack droga drogas heroina metanfetamina traficante trafico overdose cocaine weed meth",
    "sexual": "sexo sexual transar nudes pelado pelada porno pornografia orgasmo sex porn nude",
    "autoagressao": "suicidio suicidar automutilacao se_matar suicide self_harm",
    "odio": "nazista nazismo racista racismo xenofobia homofobia nazi racist",
    "armas": "arma armas pistola fuzil revolver municao gun rifle ammo",
    "tragedia": "tragedia acidente desastre vitimas massacre tragedy disaster victims",
    "controverso": "aborto fraude_eleitoral golpe_de_estado antivacina abortion election_fraud",
}
SENSITIVE_TOPICS = {k: set(norm(w) for w in v.split()) for k, v in SENSITIVE_TOPICS.items()}


def profanity_hits(text: str) -> dict:
    toks = tokens(text)
    joined = "".join(toks)
    strong = [t for t in toks if t in PROFANITY_STRONG]
    if "filhodaputa" in joined and "filhodaputa" not in strong:
        strong.append("filhodaputa")
    moderate = [t for t in toks if t in PROFANITY_MODERATE]
    return {"strong": strong, "moderate": moderate}


def sensitive_hits(text: str) -> dict:
    toks = tokens(text)
    joined = " ".join(toks)
    out = {}
    for cat, words in SENSITIVE_TOPICS.items():
        hits = sorted({w.replace("_", " ") for w in words
                       if ("_" in w and f" {w.replace('_', ' ')} " in f" {joined} ") or w in toks})
        if hits:
            out[cat] = hits
    return out


def content_words(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS and len(t) > 2]
