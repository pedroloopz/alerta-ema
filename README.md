# alerta-ema - bot da carteira (EUA + B3)

Avisos de trade no Telegram. O bot nao opera: so manda a ordem e voce executa no app.

- **EUA** (tokens de acoes na Binance Wallet + ASTER): sistema Donchian 55/10.
- **B3** (app do BRB): realizacao de +10% sobre o custo e swings por nota.
- **Futuros:** so vigia as posicoes que voce abriu.

Regras, horarios, mensagens e comandos: **[README_BOT.md](README_BOT.md)**.
Contexto para o Claude Code: [CLAUDE.md](CLAUDE.md).

## Telegram (grupo com topicos)

| Onde | Nome | O que e |
|---|---|---|
| Secret | `TELEGRAM_TOKEN` | Token do bot |
| Secret | `TELEGRAM_CHAT_ID` | Chat do bot (o grupo, comecando com -100) |
| Variable | `TELEGRAM_THREAD_ID` | Topico 📈 Trade. Vazio = manda sem topico, como antes |
| Variable | `TELEGRAM_AVISOS_THREAD_ID` | Topico ⚠️ Avisos (erros). Vazio = vai para o topico do bot |

Os numeros saem do workflow manual **descobrir-ids**.
Erros so viram aviso quando a mesma falha se repete 2 vezes seguidas, no maximo 1 por fluxo
por dia; erro passageiro (rede, 429, 5xx) nao conta (`avisos.py`, estado em `.avisos/`).

## Workflows

| Workflow | Quando |
|---|---|
| `bot` | Dias uteis, :15 e :45 das 10h as 19h de Brasilia; 21h05 todo dia (ASTER) |
| `previa` | 9h, dias uteis |
| `alerta-ema` | A cada 30 min (toque na EMA 20 de 4h nos perps) |
| `descobrir-ids`, `checar-simbolos`, backtests | So a mao (Actions → workflow → Run workflow) |

Testes: `python -m pytest tests`.
