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
    "XLEUSDT", "URNMUSDT", "USARUSDT",

    # Saude
    "LLYUSDT", "NVOUSDT", "MRNAUSDT", "HIMSUSDT",

    # Consumo e varejo
    "WMTUSDT", "HDUSDT", "DISUSDT", "NKEUSDT", "DKNGUSDT", "GMEUSDT",

    # Financeiro e pagamentos
    "JPMUSDT", "VUSDT", "BXUSDT", "PAYPUSDT",

    # Indices e alavancados
    "QQQUSDT", "UVXYUSDT", "KORUUSDT", "MUUUSDT", "KSTRUSDT",
    "SNXXUSDT", "LYTEUSDT", "CBRSUSDT", "AGPUUSDT",
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
    "energia": ["XLEUSDT", "URNMUSDT", "USARUSDT"],
    "saude": ["LLYUSDT", "NVOUSDT", "MRNAUSDT", "HIMSUSDT"],
    "consumo": ["WMTUSDT", "HDUSDT", "DISUSDT", "NKEUSDT", "DKNGUSDT", "GMEUSDT"],
    "financeiro": ["JPMUSDT", "VUSDT", "BXUSDT", "PAYPUSDT"],
    "indices": ["QQQUSDT", "UVXYUSDT", "KORUUSDT", "MUUUSDT", "KSTRUSDT",
                "SNXXUSDT", "LYTEUSDT", "CBRSUSDT", "AGPUUSDT"],
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
