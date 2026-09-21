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

## Arquivos
- `bot.py` e `b3.py` - o bot
- `carteira.json` e `carteira_b3.json` - suas posicoes. Podem ser editados a mao
- `estado_*.json` e `placar_notas*.csv` - o bot cria e atualiza sozinho
