# CLAUDE.md — alerta-ema

## O que é
Bot de alertas de trade no Telegram: carteira EUA (tokens de ações da Ondo/bStocks na Binance
Wallet + cripto ASTER) e carteira B3 (operada no app do BRB). Regras detalhadas em `README_BOT.md`
(o `README.md` descreve a versão antiga). Só spot no método principal; o bot não opera, só avisa.

| Workflow | Quando | O que faz |
|---|---|---|
| `bot` (`bot.py`, `rotacao.py`, `perps.py`) | dias úteis :15 e :45, 10h–19h de Brasília; 21h05 todo dia (ASTER) | EUA: Donchian 55/10 (`donchian.py`, calendário em `mercado.py`); rotação M3 em simulação (`estado_rotacao.json`); vigia dos futuros. B3 desligada (`B3_ATIVA`) |
| `previa` (`previa.py`) | 9h dias úteis | Prévia do dia |
| `alerta-ema` (`alerta_ema.py`) | a cada 30 min (sem rodar desde 21/09/2026) | Toque na EMA 20 (4h) nos perps |
| `backtest`, `final`, `donchian*`, `sistemas`, `teses`… | só à mão | Backtests; relatórios `relatorio_*.txt` |
| `fase3` (`backtest_fase3.py`) | só à mão | Backtest da FASE 3 (base x M1/M2/M3, EUA e B3) e resumo no tópico Trade |

- Posições: `carteira.json`, `carteira_b3.json`, `perps.json` (editáveis à mão). `estado_*.json` e `placar_notas*.csv` são do bot.
- **Cofre (repositório público):** posições, saldos e estados ficam só como `*.enc` (`cofre.py`, lista em `SENSIVEIS`). Workflow que lê: passo `python3 cofre.py abrir`; que grava: `python3 cofre.py fechar` e `git add` dos `.enc`. Chave derivada do segredo `GDRIVE_SA_JSON`. A branch `central` também vai criptografada. Mensagens dos bots não vão para o log (só com `MODO=teste`). Para editar uma posição à mão: me peça (o arquivo aberto não sobe).
- Universo EUA: `universo_ondo.json` (gerado por `montar_universo.py`).
- Dados (`fontes.py`): ações por yfinance (reserva: API do Yahoo direto); ASTER/BTC por ccxt na Binance spot via `data-api.binance.vision` (api.binance.com bloqueia o GitHub; reserva: REST direto). Falha de fonte só vai pro log.
- Cache de históricos: workflow `historico` (`historico.py`, sábado 10h17) grava na branch `dados` (diário desde 2013 + 1h; 1 commit só, regravado). Backtest: traga a branch para `dados/` e use `DADOS=dados`.
- Telegram: botão ❌ Recusar desfaz a entrada e **apaga a mensagem**; comandos (`/recusar`, `/carteira`, `/pausa`…) no `README_BOT.md`.

## Segredos e variáveis (só os nomes)
- Secrets: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` (o grupo "Central"), `GDRIVE_SA_JSON` (Drive do painel e chave do cofre), `ROBO_TOKEN`, `DISCORD_WEBHOOKS`
- Variables: `TELEGRAM_THREAD_ID` (tópico 📈 Trade), `TELEGRAM_AVISOS_THREAD_ID` (tópico ⚠️ Avisos)
- Env dos workflows/testes: `MODO`, `CRON`, `BINANCE_KLINES`, `DADOS`, `DADOS_LOCAIS`, `AGORA`

## Como testar
- `pip install -r requirements.txt yfinance lxml pytest && python -m pytest tests`
- Ensaio sem enviar nada: `MODO=teste python bot.py` (imprime as mensagens, não grava)
- Com dados salvos: `MODO=teste DADOS_LOCAIS=dados AGORA=2026-09-25T20:15Z python bot.py`
- No GitHub: Actions → `bot` → Run workflow → `modo: demo` (mensagem de teste, não grava) ou `diagnostico`

## Regras do Pedro
- Pedro não tem computador: tudo roda no GitHub Actions e se configura pelo navegador do celular (Android).
- Não quebre o que funciona. Antes de mudar, leia o código e os workflows e resuma em 5 linhas o que existe.
- Teste antes (pytest, execução de ensaio ou `workflow_dispatch`); depois commit e push direto na `main`. Push bloqueado → abra PR e faça o merge. Teste falhou e não dá para corrigir → desfaça e explique.
- Mensagens dos bots em português.
- Segredos só via `secrets`/`vars` do GitHub. Nunca chave no código nem no log; nunca peça token colado na conversa.
- Dependeu de decisão do Pedro → pare e pergunte em uma frase, com opções.
- **Custo:** não adicione chamadas à API do Claude. Use o Gemini gratuito ou nenhuma IA; se algo realmente precisar do Claude, pergunte antes.
- **Privacidade:** o bot é admin do grupo e recebe tudo. Só processa mensagens do próprio tópico (`do_meu_topico` em `bot.py`); as de outros tópicos (📸 Família, 📝 Rascunhos…) são descartadas sem salvar, logar ou enviar a nenhum serviço.
- Erros vão para ⚠️ Avisos via `avisos.py` (mesma falha 2x seguidas, 1 aviso/dia, ignora rede/429/5xx).
- Mensagem de sinal: só a **nota**, sem detalhar critérios. Nada de diário de trade.
- Não desfaça as posições atuais da carteira ao mudar regras. Backtest não garante resultado futuro.

## Como trabalhar
- **Pense antes de codar:** diga as suposições em voz alta; se o pedido aceita mais de uma leitura, pergunte em vez de escolher calado.
- **Simplicidade primeiro:** escreva o menor código que resolve. Nada de opção, camada ou recurso que não foi pedido.
- **Mudanças cirúrgicas:** toque só nas linhas necessárias. Não reformate, não renomeie, não "melhore" o código vizinho.
- **Objetivo verificável:** antes de começar, diga como saber que deu certo (teste ou execução de ensaio). Só termine quando passar.

