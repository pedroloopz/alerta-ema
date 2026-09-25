"""
Simbolos dos perps de acoes, como aparecem no app da Binance,
e o setor de cada um (usado no rodizio de setores).
"""

# Simbolos que aparecem no app. Editar aqui e a unica coisa necessaria
# para incluir ou remover um ativo.
SIMBOLOS = [
    # Semicondutores e memoria
    "NVDAUSDT", "AVGOUSDT", "AMDUSDT", "TSMUSDT", "MUUSDT", "ASMLUSDT",
    "QCOMUSDT", "ARMUSDT", "AMATUSDT", "INTCUSDT", "MRVLUSDT", "CRDOUSDT",
    "SNDKUSDT", "SKHYNIXUSDT", "SKHYUSDT", "WDCUSDT", "STXXUSDT",
    "AXTIUSDT", "AAOIUSDT", "COHRUSDT", "LITEUSDT", "GLWUSDT",
    "CXMTUSDT", "DRAMUSDT", "QNTXUSDT",

    # Infraestrutura de IA, software e nuvem
    "CRWVUSDT", "NBISUSDT", "ORCLUSDT", "NOWUSDT", "PLTRUSDT", "CRWDUSDT",
    "ADBEUSDT", "CSCOUSDT", "HPEUSDT", "ZMUSDT", "NOKUSDT",
    "OPENAIUSDT", "ANTHROPICUSDT", "ZHIPUUSDT", "MINIMAXUSDT", "UNITREEUSDT",

    # Cripto e afins
    "COINUSDT", "MSTRUSDT", "IRENUSDT", "CRCLUSDT", "HOODUSDT",
    "STRCUSDT", "BNCUSD1",

    # Big tech e consumo digital
    "AAPLUSDT", "MSFTUSDT", "GOOGLUSDT", "AMZNUSDT", "METAUSDT",
    "NFLXUSDT", "TSLAUSDT", "BABAUSDT", "EBAYUSDT", "UBERUSDT",
    "MEITUANUSDT", "KUAISHOUUSDT", "XIAOMIUSDT", "SAMSUNGUSDT",
    "HYUNDAIUSDT", "POPMARTUSDT", "FUTUUSDT", "BBXUSDT", "GPROUSD1",

    # Espaco e defesa
    "SPCXUSDT", "RKLBUSDT", "ASTSUSDT",

    # Energia e materiais
    "XLEUSDT", "URNMUSDT", "USARUSDT", "ENPHUSDT", "BEUSDT",

    # Saude
    "LLYUSDT", "NVOUSDT", "MRNAUSDT", "HIMSUSDT", "UNHUSDT",

    # Consumo e varejo
    "WMTUSDT", "HDUSDT", "DISUSDT", "NKEUSDT", "DKNGUSDT", "GMEUSDT",

    # Financeiro e pagamentos
    "JPMUSDT", "VUSDT", "BXUSDT", "PAYPUSDT",

    # Indices e alavancados
    "QQQUSDT", "UVXYUSDT", "KORUUSDT", "MUUUSDT", "KSTRUSDT",
    "SNXXUSDT", "LYTEUSDT", "CBRSUSDT", "AGPUUSDT",
]

# Posicoes que voce tem hoje na Binance Wallet. Entram em destaque no mapa
# e sempre aparecem primeiro, mesmo que saiam da lista de monitorados.
CARTEIRA = [
    "CRWVUSDT",   # CoreWeave
    "NFLXUSDT",   # Netflix
    "MRVLUSDT",   # Marvell
    "ENPHUSDT",   # Enphase
    "BEUSDT",     # Bloom Energy
    "UBERUSDT",   # Uber
    "UNHUSDT",    # UnitedHealth
]

SETORES = {
    "semicondutores": [
        "NVDAUSDT", "AVGOUSDT", "AMDUSDT", "TSMUSDT", "MUUSDT", "ASMLUSDT",
        "QCOMUSDT", "ARMUSDT", "AMATUSDT", "INTCUSDT", "MRVLUSDT", "CRDOUSDT",
        "SNDKUSDT", "SKHYNIXUSDT", "SKHYUSDT", "WDCUSDT", "STXXUSDT",
        "AXTIUSDT", "AAOIUSDT", "COHRUSDT", "LITEUSDT", "GLWUSDT",
        "CXMTUSDT", "DRAMUSDT", "QNTXUSDT",
    ],
    "infra_ia": [
        "CRWVUSDT", "NBISUSDT", "ORCLUSDT", "NOWUSDT", "PLTRUSDT", "CRWDUSDT",
        "ADBEUSDT", "CSCOUSDT", "HPEUSDT", "ZMUSDT", "NOKUSDT",
        "OPENAIUSDT", "ANTHROPICUSDT", "ZHIPUUSDT", "MINIMAXUSDT", "UNITREEUSDT",
    ],
    "cripto": ["COINUSDT", "MSTRUSDT", "IRENUSDT", "CRCLUSDT", "HOODUSDT",
               "STRCUSDT", "BNCUSD1"],
    "big_tech": ["AAPLUSDT", "MSFTUSDT", "GOOGLUSDT", "AMZNUSDT", "METAUSDT",
                 "NFLXUSDT", "TSLAUSDT", "BABAUSDT", "EBAYUSDT", "UBERUSDT",
                 "MEITUANUSDT", "KUAISHOUUSDT", "XIAOMIUSDT", "SAMSUNGUSDT",
                 "HYUNDAIUSDT", "POPMARTUSDT", "FUTUUSDT", "BBXUSDT", "GPROUSD1"],
    "espaco": ["SPCXUSDT", "RKLBUSDT", "ASTSUSDT"],
    "energia": ["XLEUSDT", "URNMUSDT", "USARUSDT", "ENPHUSDT", "BEUSDT"],
    "saude": ["LLYUSDT", "NVOUSDT", "MRNAUSDT", "HIMSUSDT", "UNHUSDT"],
    "consumo": ["WMTUSDT", "HDUSDT", "DISUSDT", "NKEUSDT", "DKNGUSDT", "GMEUSDT"],
    "financeiro": ["JPMUSDT", "VUSDT", "BXUSDT", "PAYPUSDT"],
    "indices": ["QQQUSDT", "UVXYUSDT", "KORUUSDT", "MUUUSDT", "KSTRUSDT",
                "SNXXUSDT", "LYTEUSDT", "CBRSUSDT", "AGPUUSDT"],
}

# Nome bonito do setor, usado nos titulos do mapa de calor.
NOME_SETOR = {
    "carteira": "Sua carteira",
    "semicondutores": "Semicondutores",
    "infra_ia": "Infra de IA e software",
    "cripto": "Cripto",
    "big_tech": "Big tech e consumo digital",
    "espaco": "Espaco e defesa",
    "energia": "Energia e materiais",
    "saude": "Saude",
    "consumo": "Consumo e varejo",
    "financeiro": "Financeiro e pagamentos",
    "indices": "Indices e alavancados",
    "outros": "Outros",
}

# Mesma empresa em contratos diferentes: nunca enviar os dois em sequencia
EQUIVALENTES = {
    "SKHYNIXUSDT": "SKHYNIX", "SKHYUSDT": "SKHYNIX",
    "SNDKUSDT": "SANDISK",
    "SPCXUSDT": "SPACEX",
}


def setor_de(simbolo: str) -> str:
    for setor, lista in SETORES.items():
        if simbolo in lista:
            return setor
    return "outros"


def nome_curto(simbolo: str) -> str:
    """AAPLUSDT -> AAPL, BNCUSD1 -> BNC"""
    for sufixo in ("USDT", "USD1", "USDC"):
        if simbolo.endswith(sufixo):
            return simbolo[: -len(sufixo)]
    return simbolo


def na_carteira(simbolo: str) -> bool:
    return simbolo in CARTEIRA


# Tudo que o mapa precisa varrer: monitorados + carteira, sem repetir.
def universo():
    vistos, saida = set(), []
    for s in list(CARTEIRA) + list(SIMBOLOS):
        if s not in vistos:
            vistos.add(s)
            saida.append(s)
    return saida
