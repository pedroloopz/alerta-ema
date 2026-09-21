# Alerta de toque no EMA 20 — candles de 4 horas

Avisa no Telegram quando um ativo encosta na média móvel exponencial de 20 períodos no gráfico de 4 horas.

Roda na nuvem do GitHub, de graça. Não precisa de PC ligado, corretora ou assinatura.

## Ativos monitorados

96 perps de ações da Binance, definidos em `simbolos.py`. Para incluir ou remover, edite só aquele arquivo.

| Setor | Quantos |
|---|---|
| semicondutores | 25 |
| infra_ia | 16 |
| big_tech | 19 |
| indices | 9 |
| cripto | 7 |
| consumo | 6 |
| saude | 4 |
| financeiro | 4 |
| espaco | 3 |
| energia | 3 |


## 1. Criar o bot do Telegram

1. No Telegram, procure por **@BotFather**
2. Envie `/newbot`
3. Escolha um nome qualquer (ex.: `Alerta EMA`)
4. Escolha um username terminado em `bot` (ex.: `pedro_ema_alerta_bot`)
5. Copie o **token** que ele devolve (formato `123456789:AAF...`)
6. Abra a conversa com o seu novo bot e envie `/start` — **obrigatório**, senão o bot não consegue te mandar mensagem

## 2. Descobrir o seu chat ID

1. No navegador, abra:
   `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates`
   (troque `<SEU_TOKEN>` pelo token, mantendo a palavra `bot` colada nele)
2. Procure no texto: `"chat":{"id":123456789`
3. Esse número é o seu **chat ID**

## 3. Criar o repositório

1. Crie uma conta em github.com
2. **New repository** → nome `alerta-ema` → **Create**

   > Com candle de 4h a checagem é de 30 em 30 min (~360 execuções/mês), o que **cabe nos 2.000 minutos grátis de repositório privado**. Pode deixar privado se preferir.

3. **Add file → Upload files** e envie `alerta_ema.py` e `requirements.txt`
4. **Add file → Create new file**, digite exatamente
   `.github/workflows/alerta.yml`
   e cole o conteúdo de `github-workflows-alerta.yml`

## 4. Guardar o token e o chat ID

1. **Settings → Secrets and variables → Actions**
2. **New repository secret**:
   - `TELEGRAM_TOKEN` → o token do BotFather
   - `TELEGRAM_CHAT_ID` → o número do chat ID

## 5. Ligar e testar

1. Aba **Actions** → **I understand my workflows, go ahead and enable them**
2. **alerta-ema** → **Run workflow** → **Run workflow**
3. Abra o log e confira as linhas:
   `TSLA: EMA=358.40 L=362.10 H=366.02 dist=+1,64% tocou=False`
4. Quando algum ativo estiver tocando, a mensagem chega no Telegram

## 6. Editar a lista de ativos

No topo do `alerta_ema.py`:

```python
ACOES = ["AAPL", "AMZN", "BE", ...]
```

Pelo celular: abra o arquivo no GitHub → ícone de lápis → edite → **Commit changes**.

---

## Como o bot decide long ou short

O alerta só faz sentido se o bot souber **de que lado o preço vinha** antes de encostar na média:

| Situação | Sinal | Leitura |
|---|---|---|
| Preço estava acima e caiu até a média | 🟢 LONG | média funcionando como suporte |
| Preço estava abaixo e subiu até a média | 🔴 SHORT | média funcionando como resistência |
| Preço vinha oscilando em cima da média | ⚪ SEM DIREÇÃO | não há lado definido |

Para evitar leitura errada, o script olha até 6 candles para trás e só considera aqueles que ficaram **inteiros** de um lado — candle que encostou na média não serve de referência.

### Inclinação da média

Cada alerta traz a variação da média nos últimos 5 candles. Quando o sinal aponta contra essa inclinação (ex.: SHORT com média subindo), a mensagem recebe a marca ⚠️.

### Exemplo de mensagem

```
🟢 LONG — NVDA
Encostou na EMA 20 (4h)

Motivo: preco caiu ate a media (media como suporte)
EMA: 182,40
Preco: 182,95 (0,30%)
Inclinacao da media (5 candles): 1,80% ↗
Candle: 2026-09-18 13:30:00-04:00
```

## Nota de 1 a 10

Todo sinal recebe uma nota. **Só é enviado a partir de 7,0** (constante `NOTA_MINIMA`). Os reprovados continuam sendo gravados no CSV.

| Critério | Peso | Como pontua |
|---|---|---|
| Força da tendência da média | 3,0 | proporcional à inclinação da EMA nos últimos 5 candles |
| Compatibilidade de volatilidade | 3,0 | 1 ponto se o stop não saturou no piso/teto; 1 se o stop sobrevive a 1 ATR diário; 1 se o alvo cabe em ~3 ATR diários |
| Qualidade do toque | 2,0 | 2 se o fechamento ficou a menos de 0,3 ATR da média; 1 se até 0,6 |
| Tendência estabelecida antes | 1,0 | 1 se ficou 6+ candles do mesmo lado; 0,5 se 3+ |
| Volume do candle | 1,0 | pullback com volume abaixo da média pontua mais |

O critério de volume é o mais discutível dos cinco — por isso tem o menor peso. Os dados ficam no CSV para você conferir depois se ele ajuda ou atrapalha.

## Regra de setor

Dois sinais seguidos do mesmo setor não são enviados: o segundo fica **retido, não descartado**. A cada rodada (30 min) ele é reavaliado com dados novos e é enviado assim que outro setor passar na frente — desde que continue adequado.

"Adequado" significa três coisas ao mesmo tempo:

1. A nota continua **7,0 ou mais**
2. O preço ainda está **colado na média** — no máximo 0,5 ATR de distância. Se o preço encostou e já disparou para longe dentro do mesmo candle, a entrada não vale mais e o sinal morre
3. O sinal continua **a favor da inclinação** da média

O rodízio de setor **reinicia a cada dia**: o último setor de ontem não bloqueia o primeiro sinal de hoje.

Os candidatos são ordenados por nota, então o melhor de cada setor passa na frente.

Setores: semicondutores, infra_ia, cripto, energia, petroleo, big_tech, consumo, financeiro, saude, cambio.

GOOG e GOOGL são a mesma empresa — o script nunca envia os dois em sequência.

## Registro em CSV

Todo sinal avaliado vai para `sinais.csv`, inclusive os não enviados, com o motivo: nota baixa, setor repetido ou contra a inclinação da média. O workflow commita o arquivo a cada rodada. Em dois meses você tem o backtest da sua própria regra.

## Fonte de dados

A API pública da Binance, sem chave e sem cadastro. Três ganhos sobre o Yahoo:

1. **É o preço que você opera.** O perp descola da ação: em junho de 2026, o perp da Samsung na Binance negociou em média 0,93% acima do da Hyperliquid, e o de SK Hynix chegou a 2,3% de diferença. Com stop de 2% a 3%, calcular a média no preço errado invalida o sinal.
2. **Candle de 4h nativo.** Acabou o agrupamento manual e o problema de alinhamento.
3. **Cobre o que não tem ação listada.** SPCX, OPENAI, ANTHROPIC, UNITREE, MINIMAX, ZHIPU e CXMT não existem no Yahoo — só a exchange tem preço deles.

⚠️ A Binance bloqueia requisições de IPs americanos com **HTTP 451**, e os servidores do GitHub Actions ficam nos EUA. Rode `checar_simbolos.py` antes de tudo: se der 451, o robô não funciona no GitHub e precisa rodar no seu PC ou num servidor fora dos EUA.


## Limitações honestas

| Ponto | Situação |
|---|---|
| Bloqueio geográfico | Binance responde 451 a IP dos EUA — pode inviabilizar o GitHub Actions |
| Pontualidade | O agendamento do GitHub pode atrasar 5 a 15 min |
| Precisão do toque | Verifica a cada 30 min, não tick a tick |
| Liquidez | Vários desses contratos giram poucos milhares de dólares por dia; spread e slippage comem parte do resultado |
| Atraso do dado | Nenhum — o preço vem da própria exchange |


### Scripts

- `checar_simbolos.py` — confere quais símbolos existem na API antes de ligar o robô
- `simbolos.py` — lista e setores
- `alerta_ema.py` — o robô

#### Antigo: 
Crie uma conta gratuita na Alpaca (alpaca.markets), pegue as chaves de API do modo *paper* e troque o `yf.download` por uma chamada ao endpoint `/v2/stocks/bars` com `feed=iex` e `timeframe=4Hour` — a Alpaca já entrega candle de 4h pronto e em tempo real.

### Se o Yahoo parar de responder

O Yahoo às vezes bloqueia requisições vindas de servidores de nuvem. Se o log acusar falha repetida, migre para a Alpaca (acima) ou para a API gratuita da Finnhub.
