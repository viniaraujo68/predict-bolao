"""Fonte única da verdade dos nomes das seleções.

O store da bet365 e o scoreboard da ESPN gravam os times em grafias e idiomas
diferentes (Brazil/Brasil, Algeria/Argélia, Congo DR/RD Congo…). Aqui cada time
tem UM nome canônico (em português) e a lista de apelidos conhecidos (inglês e
variações). Aplicando `canonical_team` nos dois lados — na captura (bet365) e na
leitura (ESPN) — todos os nomes convergem pra mesma grafia e o casamento vira
igualdade exata, sem heurística de similaridade.
"""

from __future__ import annotations

import unicodedata


def normalize(s: str) -> str:
    """Forma de comparação: sem acento, minúsculo, sem hífen/ponto, 1 espaço."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("-", " ").replace(".", " ")
    return " ".join(s.split())


# Nome canônico (PT, com acento) -> apelidos aceitos (EN e variações de grafia).
# O próprio canônico também é aceito como entrada (não precisa repetir aqui).
_TEAMS: dict[str, list[str]] = {
    "Alemanha": ["Germany"],
    "Argentina": [],
    "Argélia": ["Algeria"],
    "Arábia Saudita": ["Saudi Arabia"],
    "Austrália": ["Australia"],
    "Áustria": ["Austria"],
    "Bélgica": ["Belgium"],
    "Bósnia e Herzegovina": ["Bosnia-Herzegovina", "Bosnia and Herzegovina"],
    "Brasil": ["Brazil"],
    "Cabo Verde": ["Cape Verde"],
    "Canadá": ["Canada"],
    "Catar": ["Qatar"],
    "Colômbia": ["Colombia"],
    "Coreia do Sul": ["South Korea", "Korea Republic", "República da Coreia"],
    "Costa do Marfim": ["Ivory Coast", "Cote d'Ivoire"],
    "Croácia": ["Croatia"],
    "Curaçao": ["Curacao"],
    "Egito": ["Egypt"],
    "Equador": ["Ecuador"],
    "Escócia": ["Scotland"],
    "Espanha": ["Spain"],
    "Estados Unidos": ["United States", "USA", "EUA"],
    "França": ["France"],
    "Gana": ["Ghana"],
    "Haiti": [],
    "Holanda": ["Netherlands", "Países Baixos"],
    "Inglaterra": ["England"],
    "Irã": ["Iran"],
    "Iraque": ["Iraq"],
    "Japão": ["Japan"],
    "Jordânia": ["Jordan"],
    "Marrocos": ["Morocco"],
    "México": ["Mexico"],
    "Noruega": ["Norway"],
    "Nova Zelândia": ["New Zealand"],
    "Panamá": ["Panama"],
    "Paraguai": ["Paraguay"],
    "Portugal": [],
    "República Democrática do Congo": ["Congo DR", "DR Congo", "RD Congo"],
    "República Tcheca": ["Czechia", "Tchéquia", "Czech Republic"],
    "Senegal": [],
    "Suécia": ["Sweden"],
    "Suíça": ["Switzerland"],
    "Tunísia": ["Tunisia"],
    "Turquia": ["Türkiye", "Turkey"],
    "Uruguai": ["Uruguay"],
    "Uzbequistão": ["Uzbekistan"],
    "África do Sul": ["South Africa"],
}

# Índice normalizado (qualquer grafia/idioma) -> nome canônico.
_BY_NORM: dict[str, str] = {}
for _canon, _aliases in _TEAMS.items():
    for _name in (_canon, *_aliases):
        _BY_NORM[normalize(_name)] = _canon


def canonical_team(name: str) -> str:
    """Nome canônico (PT) de `name`, venha da bet365 ou da ESPN, em qualquer
    grafia/idioma. Nome desconhecido (ex.: placeholder '2º do Grupo A') volta
    como veio, só aparado."""
    return _BY_NORM.get(normalize(name), (name or "").strip())
