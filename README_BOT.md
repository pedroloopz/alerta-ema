# Bot da carteira - EUA (Binance Wallet) + B3 (BRB)

## EUA - sistema Donchian 55/10

| Regra | Como funciona |
|---|---|
| Universo | `universo_ondo.json`: 100 acoes tokenizadas (Ondo, sufixo "on"; ou bStocks da Binance, sufixo "B") + ASTER. Fonte e data da consulta no proprio arquivo |
| Entrada (acao) | Preco passa da **maxima dos ultimos 55 pregoes**, com o **SPY acima da media de 200 dias** no fechamento anterior |
| Entrada (ASTER) | Candle diario **fecha** acima da maxima dos 55 dias anteriores, com o **BTC acima da media de 200 dias**. Compra na hora (21h de Brasilia) |
| Mais sinais que vagas | Prioridade para a maior alta em 6 meses (126 pregoes) |
| Saida | **Fechamento abaixo da minima dos 10 pregoes anteriores** -> vende 100% na abertura seguinte. Nao existe mais a realizacao de +10% |
| Vagas | **7 no total**, no maximo **1 cripto** (ASTER). Posicoes antigas ("legado") ocupam vaga e so saem pela regra de saida |
| Tamanho | Patrimonio EUA / 7, limitado ao caixa USDT. Ordem minima US$ 50. Quantidade arredondada para baixo (acao: 4 casas; ASTER: 0,01) |

Backtest que sustenta as regras: `relatorio_final.txt` (workflow manual `final`).

Precos: o app do Binance Wallet mostra o token Ondo pelo preco **da acao** (ex.: UNHon
~ preco do UNH, apesar de 1 token valer 1,023 acao). O bot usa o mesmo padrao: preco da
acao no Yahoo e valor em US$. Na duvida, compre pelo **valor em US$** da mensagem.

## Horarios (Brasilia)

O codigo decide pelo relogio de Nova York (zoneinfo), nao pelo cron. NY sai do horario
de verao em **01/11/2026**: o fechamento passa de 17h para **18h** em Brasilia.
Feriados e pregoes curtos da bolsa americana (13h de NY) estao em `mercado.py`.

| Quando | EUA | B3 |
|---|---|---|
| 9h | Previa do dia (`previa.py`): quem esta a menos de 3% da saida, vagas e caixa | Idem |
| Pregao de NY aberto (checagem a cada 30 min, :15 e :45) | 🟢 Compra quando rompe a maxima de 55 dias | 10h15 a 17h15, so com a B3 aberta |
| Logo depois do fechamento de NY (17h15 ou 18h15) | 🔴 "Vender na abertura" + heatmap do dia (sem som) | Fechamento B3 |
| 21h05 todo dia (inclusive fim de semana) | ASTER: saida e entrada no candle diario | - |

Obs.: o GitHub pode atrasar execucoes agendadas. Por isso o EUA roda tambem aos :45 (so EUA;
B3 e futuros seguem de hora em hora, aos :15). O bot nao repete ordem e recupera a fase perdida na
execucao seguinte (saida marcada vende pela abertura real do dia).

## Mensagens EUA

- **Compra** (com som, botao ❌ Recusar): ticker, token, preco de referencia, "compre ate"
  (1% acima), valor em US$, quantidade e preco de saida atual.
- **Venda** (com som): ticker, % a vender, unidades e lucro estimado do trade.
- **Heatmap** (sem som): um bloco por posicao (area = valor, cor = variacao do dia),
  patrimonio, lucro do dia e desde o inicio, caixa, vagas, maior alta/queda, novas maximas,
  quem esta a menos de 3% da saida, sinais do dia, balancos nos proximos 2 pregoes,
  SPY, QQQ e BTC.
- O bot nao apaga mensagens antigas (use a exclusao automatica do Telegram). So o ❌ Recusar apaga.

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
| Botao **❌ Recusar** | Desfaz a compra, apaga a mensagem, silencia o ativo por 5 dias | Idem |
| `/recusar TICKER` | Mesmo efeito do botao | Idem |
| `/comprei NVDA 1,5720 629,26` | Registra a compra real (unidades e preco) | - |
| `/vendi NVDA 1,5720 640,10` | Registra a venda real (corrige o caixa se o bot ja tinha vendido pela abertura) | - |
| `/nao` | Desfaz a ultima leva de ordens | - |
| `/nao_br` | - | Desfaz a ultima ordem |
| `/reserva 1200` | Define o caixa USDT (a diferenca conta como aporte/retirada, nao como lucro) | - |
| `/reserva_br 3000` | - | Define a reserva em R$ (conta como aporte) |
| `/unidades TICKER N` | Corrige a quantidade | Idem |
| `/carteira` | Mostra a carteira | Idem |
| `/pausa 7` | Sem compras novas por 7 dias (saidas continuam). `/pausa 0` encerra | - |
| `/perps` | Situacao dos futuros | - |
| `/fechei EWZ` | Tira o futuro do acompanhamento | - |

## Modos (workflow `bot`, botao Run workflow)

`auto` (padrao) | `tudo` (pregao + fechamento + ASTER agora) | `pregao` | `fechamento` |
`cripto` | `diagnostico` (posicao a posicao: preco, lucro, minima de 10 dias, distancia, decisao) |
`demo` (uma mensagem de teste + heatmap de exemplo, nao grava nada).

Localmente: `MODO=teste python bot.py` imprime as mensagens em vez de mandar e nao grava nada.
Simulacao com dados salvos: `MODO=teste DADOS_LOCAIS=dados AGORA=2026-09-25T20:15Z python bot.py`.
Testes: `python -m pytest tests`.

## Arquivos
- `bot.py` (EUA + nucleo), `donchian.py` (regras), `mercado.py` (calendario), `heatmap_eua.py`,
  `b3.py`, `perps.py` e `previa.py`
- `carteira.json` (EUA, formato novo por posicao), `carteira_b3.json` e `perps.json` - suas posicoes.
  `carteira_antiga.json` = backup do formato anterior
- `universo_ondo.json` - lista de ativos EUA (montada por `montar_universo.py`)
- `estado_*.json` e `placar_notas*.csv` - o bot cria e atualiza sozinho
