"""
Resumo diario no Telegram: um mapa de calor dos seus contratos,
colorido pela distancia ate a EMA 20 de 4h.

Quadrado palido = colado na media = candidato a sinal.
Verde = acima da media.  Vermelho = abaixo.
"""

import os
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

from simbolos import SIMBOLOS, SETORES, setor_de, nome_curto
from alerta_ema import baixar_candles, PERIODO_EMA, TOKEN, CHAT_ID

ARQUIVO_PNG = "mapa.png"
COLUNAS = 8


def coletar():
    dados = []
    for simbolo in SIMBOLOS:
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
        })
    return pd.DataFrame(dados)


def desenhar(df):
    ordem = list(SETORES.keys())
    df["ordem"] = df["setor"].apply(lambda s: ordem.index(s) if s in ordem else 99)
    df = df.sort_values(["ordem", "dist"], ascending=[True, False]).reset_index(drop=True)

    linhas = int(np.ceil(len(df) / COLUNAS))
    fig, ax = plt.subplots(figsize=(COLUNAS * 1.35, linhas * 1.1 + 0.9))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    limite = 4.0   # saturacao da cor em +/- 4%
    mapa = plt.get_cmap("RdYlGn")

    for k, linha in df.iterrows():
        col, lin = k % COLUNAS, k // COLUNAS
        valor = np.clip(linha["dist"] / limite, -1, 1)
        cor = mapa((valor + 1) / 2)

        ax.add_patch(plt.Rectangle((col, -lin), 0.95, 0.95,
                                   facecolor=cor, edgecolor="#0d1117", linewidth=2))
        if linha["tocando"]:
            ax.add_patch(plt.Rectangle((col, -lin), 0.95, 0.95, fill=False,
                                       edgecolor="white", linewidth=3))

        texto = "#111" if abs(valor) < 0.55 else "#fff"
        ax.text(col + 0.475, -lin + 0.58, linha["curto"][:9],
                ha="center", va="center", fontsize=8.5, weight="bold", color=texto)
        ax.text(col + 0.475, -lin + 0.30, f"{linha['dist']:+.2f}%".replace(".", ","),
                ha="center", va="center", fontsize=7.5, color=texto)

    ax.set_xlim(-0.1, COLUNAS)
    ax.set_ylim(-linhas + 0.9, 2.0)
    ax.axis("off")
    agora = datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")
    ax.text(0, 1.62, f"Distancia ate a EMA 20 (4h)  -  {agora}",
            fontsize=12, weight="bold", color="white")
    ax.text(0, 1.25, "palido = colado na media | borda branca = tocando agora",
            fontsize=8.5, color="#8b949e")

    plt.tight_layout()
    plt.savefig(ARQUIVO_PNG, dpi=110, facecolor=fig.get_facecolor())
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


def main():
    df = coletar()
    if df.empty:
        print("Sem dados.")
        return
    desenhar(df)

    perto = df.reindex(df["dist"].abs().sort_values().index).head(6)
    linhas = [f"  {r['curto']:10s} {r['dist']:+.2f}%".replace(".", ",")
              for _, r in perto.iterrows()]

    legenda = (
        f"RESUMO DIARIO\n\n{resumo_do_csv()}\n\n"
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
