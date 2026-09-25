"""
Resumo diario no Telegram: mapa de calor dos contratos,
colorido pela distancia ate a EMA 20 de 4h.

Leitura do mapa:
  quadrado palido = colado na media = candidato a sinal
  verde = acima da media   |   vermelho = abaixo
  borda branca = tocando a media agora
  borda dourada + ponto = posicao que voce tem na carteira

A primeira faixa e sempre a sua carteira; depois vem os setores.
"""

import os
from datetime import datetime, timezone, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import pandas as pd
import requests

from simbolos import (SETORES, NOME_SETOR, CARTEIRA, setor_de, nome_curto,
                      na_carteira, universo)
from alerta_ema import baixar_candles, PERIODO_EMA, TOKEN, CHAT_ID

ARQUIVO_PNG = "mapa.png"
COLUNAS = 8
LIMITE = 4.0            # saturacao da cor em +/- 4%
FUNDO = "#0d1117"
CINZA = "#8b949e"

# Verde/vermelho com meio escuro (o RdYlGn puro deixa o centro amarelo berrante)
CORES = LinearSegmentedColormap.from_list(
    "dist", ["#b3202c", "#e06a5a", "#d9d2c4", "#4cae62", "#1a7a3c"])


def coletar():
    dados = []
    for simbolo in universo():
        try:
            c4 = baixar_candles(simbolo, "4h", 60)
        except Exception as e:
            print(f"{simbolo}: {e}")
            continue
        if len(c4) < PERIODO_EMA + 2:
            continue
        ema = c4["Close"].ewm(span=PERIODO_EMA, adjust=False).mean()
        atual = c4.iloc[-1]
        dist = (atual["Close"] / ema.iloc[-1] - 1) * 100
        dados.append({
            "simbolo": simbolo, "curto": nome_curto(simbolo),
            "setor": setor_de(simbolo), "dist": float(dist),
            "tocando": bool(atual["Low"] <= ema.iloc[-1] <= atual["High"]),
            "carteira": na_carteira(simbolo),
        })
    return pd.DataFrame(dados)


def _blocos(df):
    """Lista de (titulo, sub-dataframe) na ordem de desenho."""
    saida = []
    carteira = df[df["carteira"]].sort_values("dist", ascending=False)
    if not carteira.empty:
        saida.append(("carteira", carteira))
    for setor in SETORES:
        sub = df[(df["setor"] == setor) & (~df["carteira"])]
        if not sub.empty:
            saida.append((setor, sub.sort_values("dist", ascending=False)))
    resto = df[(~df["carteira"]) & (~df["setor"].isin(SETORES))]
    if not resto.empty:
        saida.append(("outros", resto.sort_values("dist", ascending=False)))
    return saida


def desenhar(df):
    blocos = _blocos(df)

    # Altura: cada bloco gasta 1 linha de titulo + as linhas de quadrados
    linhas_total = sum(1 + int(np.ceil(len(sub) / COLUNAS)) for _, sub in blocos)

    fig, ax = plt.subplots(figsize=(COLUNAS * 1.45, linhas_total * 1.02 + 1.8))
    fig.patch.set_facecolor(FUNDO)
    ax.set_facecolor(FUNDO)

    y = 0.0
    for titulo, sub in blocos:
        y -= 1.0
        ax.text(0, y + 0.30, " ".join(NOME_SETOR.get(titulo, titulo).upper()),
                fontsize=9, weight="bold", color="#c9d1d9")
        ax.plot([0, COLUNAS - 0.05], [y + 0.12, y + 0.12],
                color="#21262d", linewidth=1.2)

        for k, (_, linha) in enumerate(sub.iterrows()):
            col, lin = k % COLUNAS, k // COLUNAS
            topo = y - lin * 1.0
            valor = float(np.clip(linha["dist"] / LIMITE, -1, 1))
            cor = CORES((valor + 1) / 2)

            ax.add_patch(plt.Rectangle((col, topo - 0.95), 0.95, 0.95,
                                       facecolor=cor, edgecolor=FUNDO,
                                       linewidth=2, zorder=2))
            if linha["carteira"]:
                ax.add_patch(plt.Rectangle((col, topo - 0.95), 0.95, 0.95,
                                           fill=False, edgecolor="#e3b341",
                                           linewidth=2.6, zorder=3))
            if linha["tocando"]:
                ax.add_patch(plt.Rectangle((col + 0.06, topo - 0.89), 0.83, 0.83,
                                           fill=False, edgecolor="white",
                                           linewidth=2.2, zorder=4))

            texto = "#111418" if abs(valor) < 0.55 else "#ffffff"
            ax.text(col + 0.475, topo - 0.38, linha["curto"][:9],
                    ha="center", va="center", fontsize=9,
                    weight="bold", color=texto, zorder=5)
            ax.text(col + 0.475, topo - 0.66, f"{linha['dist']:+.2f}%".replace(".", ","),
                    ha="center", va="center", fontsize=7.8, color=texto, zorder=5)

        y -= int(np.ceil(len(sub) / COLUNAS)) * 1.0

    ax.set_xlim(-0.12, COLUNAS + 0.02)
    ax.set_ylim(y - 0.9, 2.5)
    ax.axis("off")

    fuso_br = timezone(timedelta(hours=-3))
    agora = datetime.now(fuso_br).strftime("%d/%m/%Y %H:%M")
    ax.text(0, 1.85, "Distancia ate a EMA 20 (4h)",
            fontsize=15, weight="bold", color="white")
    ax.text(0, 1.42, f"{agora} (horario de Brasilia)  -  {len(df)} ativos",
            fontsize=9, color=CINZA)
    ax.text(0, 1.08,
            "palido = colado na media   |   borda branca = tocando agora   "
            "|   borda dourada = sua carteira",
            fontsize=8.5, color=CINZA)

    # Barra de cor no proprio cabecalho, para o numero ter referencia visual
    x0, x1, base, alt = COLUNAS - 3.3, COLUNAS - 0.05, 1.62, 0.22
    ax.imshow(np.linspace(0, 1, 256).reshape(1, -1), cmap=CORES,
              extent=(x0, x1, base, base + alt), aspect="auto", zorder=6)
    ax.add_patch(plt.Rectangle((x0, base), x1 - x0, alt, fill=False,
                               edgecolor="#21262d", linewidth=1, zorder=7))
    for x, rotulo, alinha in ((x0, f"-{LIMITE:.0f}%", "left"),
                              ((x0 + x1) / 2, "na media", "center"),
                              (x1, f"+{LIMITE:.0f}%", "right")):
        ax.text(x, base - 0.16, rotulo, fontsize=7.5, color=CINZA,
                ha=alinha, va="center")

    plt.savefig(ARQUIVO_PNG, dpi=115, facecolor=fig.get_facecolor(),
                bbox_inches="tight", pad_inches=0.35)
    plt.close()


def resumo_do_csv():
    if not os.path.exists("sinais.csv"):
        return "Ainda sem registro de sinais."
    try:
        s = pd.read_csv("sinais.csv")
    except Exception:
        return "sinais.csv ilegivel."
    if s.empty:
        return "Nenhum sinal registrado ainda."

    hoje = datetime.now(timezone.utc).date().isoformat()
    do_dia = s[s["horario_utc"].astype(str).str.startswith(hoje)]
    if do_dia.empty:
        return f"Nenhum toque hoje. Total acumulado: {len(s)} registros."

    enviados = (do_dia["enviado"] == "sim").sum()
    partes = [f"Toques hoje: {len(do_dia)}", f"Enviados: {enviados}"]
    motivos = do_dia[do_dia["enviado"] == "nao"]["motivo_nao_envio"].value_counts()
    for motivo, n in motivos.head(4).items():
        partes.append(f"  {n}x {str(motivo)[:45]}")
    return "\n".join(partes)


def bloco_carteira(df):
    sub = df[df["carteira"]]
    if sub.empty:
        return "Carteira: nenhum ativo da lista respondeu."
    sub = sub.reindex(sub["dist"].abs().sort_values().index)
    linhas = [f"  {r['curto']:8s} {r['dist']:+.2f}%".replace(".", ",")
              for _, r in sub.iterrows()]
    faltando = [nome_curto(s) for s in CARTEIRA
                if s not in set(df["simbolo"])]
    if faltando:
        linhas.append("  sem dado: " + ", ".join(faltando))
    return "Sua carteira (distancia da media):\n" + "\n".join(linhas)


def main():
    df = coletar()
    if df.empty:
        print("Sem dados.")
        return
    desenhar(df)

    perto = df.reindex(df["dist"].abs().sort_values().index).head(6)
    linhas = [f"  {r['curto']:8s} {r['dist']:+.2f}%".replace(".", ",")
              for _, r in perto.iterrows()]

    legenda = (
        f"RESUMO DIARIO\n\n{resumo_do_csv()}\n\n"
        f"{bloco_carteira(df)}\n\n"
        f"Mais proximos da media agora:\n" + "\n".join(linhas)
    )

    with open(ARQUIVO_PNG, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": legenda[:1000]},
            files={"photo": f}, timeout=60)
    print("Telegram:", r.status_code, r.text[:200])


if __name__ == "__main__":
    main()
