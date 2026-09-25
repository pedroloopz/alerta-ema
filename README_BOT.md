# Bot da carteira - EUA (Binance Wallet) + B3 (BRB)

## Horarios (Brasilia, dias uteis)

| Horario | EUA | B3 |
|---|---|---|
| De hora em hora | 10h15 a 19h15: realizacao de +10% e alerta de 20% nos swings | 10h15 a 17h15, so com a B3 aberta |
| 15h15 | - | Varredura: swings, trocas, revisoes |
| 16h15 | Varredura: swings, trocas, revisoes | - |
| 18h15 | Fechamento: mapa + vencedores/perdedores + carteira | Fechamento B3 |

Feriado de um mercado nao afeta o outro.

## Regras - EUA
- **+10% numa posicao:** vende o excesso acima de US$ 1.000 → reserva USDT
- **Swing:** 2 ATR abaixo da EMA 20, nota 6+. Nota 8+ = ate US$ 500; 6 a 7,9 = ate US$ 250
- **Troca:** so em posicao acima de US$ 1.000, com 10+ pregoes, para grafico 7+ e 2 pontos melhor

## Regras - B3
- **Base de cada posicao = o que voce pagou** (preco medio x quantidade)
- **+10% sobre o custo:** vende o excesso em acoes inteiras → reserva R$
- **Swing:** mesma regra, nota 8+ = ate R$ 2.000; 6 a 7,9 = ate R$ 1.000
- **Lotes:** abaixo de 100 acoes, o bot indica o fracionario (ticker + F)
- **Trava da isencao:** realizacao ou troca que passaria de R$ 20 mil em vendas de acoes no mes fica adiada para o mes seguinte. Saida de swing nao e travada
- **BDR (INBR32 e final 31-39):** sem isencao. Cada venda mostra o IR estimado
- **Universo:** IBrX-100 + Small Caps direto da B3 (~180 a 200 acoes). Se a B3 nao responder, lista de reserva

## Futuros (perps)

O bot **nao abre posicao alavancada** — quem abre e voce. Ele vigia:

| Aviso | Quando |
|---|---|
| 🚨 Liquidacao | Distancia de 15%, 10% e 5% ate o preco de liquidacao |
| 🛑 Perda | Perdeu 30% e 50% da margem |
| 🎯 Ganho | Ganhou 30% e 60% da margem — vale realizar metade |
| 🌙 Fim de semana | Toda sexta: o ativo para, o perp nao. Mostra o gap que zera a margem |
| 📅 Evento | Contagem regressiva das eleicoes (4/10 e 25/10), com o gap que zera cada posicao |
| ⚡ Alavancagem | Marca a posicao acima do seu padrao de 2x |
| 📌 Protecao | Uma vez por posicao: stop e alvo sugeridos pelo ATR, ja em % da margem. Avisa se a alavancagem nao deixa espaco para stop antes da liquidacao |
| 📉 Trocar | Grafico do ativo em 3/10 ou menos: sugere fechar e liberar a vaga |
| 🎣 Vaga livre | Sempre 2 futuros. Sobrou vaga, procura candidato com nota 7+ e manda entrada, stop, alvo e liquidacao estimada |

Cada aviso sai no maximo uma vez por dia para cada posicao. Posicao fechada: `/fechei EWZ`.
O foco da carteira e o spot: os futuros ficam limitados a 2 posicoes, sempre.
Posicao nova: edite `perps.json` (tamanho, entrada, margem, liquidacao e alavancagem estao no app).

## Comandos no Telegram (respondidos na proxima execucao)

| Comando | EUA | B3 |
|---|---|---|
| Botao **❌ Recusar** | Desfaz a entrada, apaga a mensagem, silencia 5 dias | Idem |
| `/recusar TICKER` | Mesmo efeito do botao | Idem |
| `/nao` | Desfaz a ultima ordem | - |
| `/nao_br` | - | Desfaz a ultima ordem |
| `/reserva 1200` | Corrige a reserva USDT | - |
| `/reserva_br 3000` | - | Define a reserva em R$ (conta como aporte) |
| `/unidades TICKER N` | Corrige a quantidade | Idem |
| `/carteira` | Mostra a carteira | Idem |
| `/perps` | Situacao dos futuros | - |
| `/fechei EWZ` | Tira o futuro do acompanhamento | - |

## Arquivos
- `bot.py`, `b3.py` e `perps.py` - o bot
- `carteira.json`, `carteira_b3.json` e `perps.json` - suas posicoes. Podem ser editados a mao
- `estado_*.json` e `placar_notas*.csv` - o bot cria e atualiza sozinho
