# CLAUDE.md — alerta-ema

## O que é
Bot de alertas de trade no Telegram: carteira EUA (tokens de ações da Ondo/bStocks na Binance
Wallet + cripto ASTER) e carteira B3 (operada no app do BRB). Regras detalhadas em `README_BOT.md`
(o `README.md` descreve a versão antiga). Só spot no método principal; o bot não opera, só avisa.

| Workflow | Quando | O que faz |
|---|---|---|
| `bot` (`bot.py`, `b3.py`, `perps.py`) | dias úteis :15 e :45, 10h–19h de Brasília; 21h05 todo dia (ASTER) | EUA: Donchian 55/10 (`donchian.py`, calendário em `mercado.py`); B3; vigia dos futuros |
| `previa` (`previa.py`) | 9h dias úteis | Prévia do dia |
| `alerta-ema` (`alerta_ema.py`) | a cada 30 min | Toque na EMA 20 (4h) nos perps |
| `backtest`, `final`, `donchian*`, `sistemas`, `teses`… | só à mão | Backtests; relatórios `relatorio_*.txt` |

- Posições: `carteira.json`, `carteira_b3.json`, `perps.json` (editáveis à mão). `estado_*.json` e `placar_notas*.csv` são do bot.
- Universo EUA: `universo_ondo.json` (gerado por `montar_universo.py`).
- Telegram: botão ❌ Recusar desfaz a entrada e **apaga a mensagem**; comandos (`/recusar`, `/carteira`, `/pausa`…) no `README_BOT.md`.

## Segredos e variáveis (só os nomes)
- Secrets: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` (o grupo "Central")
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

