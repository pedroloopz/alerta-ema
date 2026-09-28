"""
HEATMAP DE FIM DO DIA - carteira EUA.

Um bloco por posicao: area = valor em US$, cor = variacao do dia
(vermelho = queda, cinza = estavel, verde = alta; satura em +-5%).
Cada bloco traz ticker e % escritos: a cor nunca e a unica informacao.
"""

import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

plt.rcParams["text.parse_math"] = False

FUNDO = "#1a1a19"
TEXTO = "#f0efec"
TEXTO_2 = "#a8a7a2"
MAPA = LinearSegmentedColormap.from_list("rvg", ["#d64545", "#383835", "#2e9e4f"])
SATURA = 0.05

num = lambda x, c=2: f"{x:,.{c}f}".replace(",", "X").replace(".", ",").replace("X", ".")
pct = lambda x: f"{x * 100:+.1f}%".replace(".", ",")


def _sem_emoji(txt):
    """A fonte do grafico nao tem emoji: tira os simbolos e mantem o texto."""
    return "".join(ch for ch in txt if ord(ch) < 0x2000).strip()


# ------------------------------------------------ treemap "squarified"
def _pior(linha, lado):
    s = sum(linha)
    return max(max(lado * lado * r / (s * s), (s * s) / (lado * lado * r)) for r in linha)


def _dispor(linha, x, y, w, h):
    s = sum(linha)
    out = []
    if w >= h:                      # coluna na esquerda
        larg = s / h
        yy = y
        for r in linha:
            alt = r / larg
            out.append((x, yy, larg, alt))
            yy += alt
        return out, (x + larg, y, w - larg, h)
    alt = s / w                     # linha no topo
    xx = x
    for r in linha:
        larg = r / alt
        out.append((xx, y, larg, alt))
        xx += larg
    return out, (x, y + alt, w, h - alt)


def squarify(valores, x, y, w, h):
    """Retangulos (x, y, w, h) na ordem dos valores (ja ordenados do maior ao menor)."""
    tot = sum(valores)
    if tot <= 0:
        return []
    area = [v * w * h / tot for v in valores]
    rets, linha, ret = [], [], (x, y, w, h)
    for a in area:
        lado = min(ret[2], ret[3])
        if not linha or _pior(linha + [a], lado) <= _pior(linha, lado):
            linha.append(a)
        else:
            r, ret = _dispor(linha, *ret)
            rets += r
            linha = [a]
    if linha:
        r, _ = _dispor(linha, *ret)
        rets += r
    return rets


# ------------------------------------------------ figura
def desenhar(itens, titulo, legenda, caminho="heatmap_eua.png"):
    """itens: lista de dicts {ticker, valor, var_dia, marca} (marca: texto curto opcional).
    legenda: linhas de texto do rodape."""
    itens = sorted([i for i in itens if i["valor"] > 0], key=lambda i: -i["valor"])
    linhas = []
    for k, x in enumerate(legenda):
        partes = textwrap.wrap(_sem_emoji(x), 98, subsequent_indent="   ") or [""]
        linhas += partes if k >= 2 else partes[:1]
    rodape = 0.5 + 0.28 * len(linhas)                  # polegadas
    alt_fig = 6.2 + rodape
    fig = plt.figure(figsize=(8, alt_fig), dpi=130)
    fig.patch.set_facecolor(FUNDO)
    base = (rodape + 0.2) / alt_fig
    ax = fig.add_axes([0.03, base, 0.94, 1 - base - 0.9 / alt_fig])
    ax.set_facecolor(FUNDO)
    rets = squarify([i["valor"] for i in itens], 0, 0, 100, 100)
    for it, (x, y, w, h) in zip(itens, rets):
        v = max(-1, min(1, it["var_dia"] / SATURA))
        ax.add_patch(plt.Rectangle((x + 0.4, y + 0.4), w - 0.8, h - 0.8,
                                   facecolor=MAPA((v + 1) / 2), edgecolor=FUNDO, linewidth=2))
        grande = min(w, h)
        fs = max(7, min(20, grande * 0.45))
        cx, cy = x + w / 2, y + h / 2
        ax.text(cx, cy - fs * 0.12, it["ticker"], ha="center", va="center", color=TEXTO,
                fontsize=fs, weight="bold")
        ax.text(cx, cy + fs * 0.22, pct(it["var_dia"]), ha="center", va="center", color=TEXTO,
                fontsize=fs * 0.72)
        if it.get("marca") and grande > 14:
            ax.text(cx, cy + fs * 0.48, it["marca"], ha="center", va="center", color=TEXTO,
                    fontsize=max(6, fs * 0.45))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.invert_yaxis()
    ax.axis("off")
    fig.text(0.03, 1 - 0.35 / alt_fig, titulo, color=TEXTO, fontsize=15, weight="bold", va="top")
    fig.text(0.03, 1 - 0.68 / alt_fig, "area = valor da posicao | cor = variacao do dia (satura em 5%)",
             color=TEXTO_2, fontsize=8.5, va="top")
    for k, lin in enumerate(linhas):
        fig.text(0.03, (rodape - 0.1 - k * 0.28) / alt_fig, lin, color=TEXTO if k < 2 else TEXTO_2,
                 fontsize=10.5 if k < 2 else 9, weight="bold" if k == 0 else "normal", va="top")
    fig.savefig(caminho, facecolor=FUNDO)
    plt.close(fig)
    return caminho
