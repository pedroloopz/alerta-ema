# Bot da carteira

## Horarios (Brasilia, dias uteis)

| Horario | O que faz | Manda mensagem? |
|---|---|---|
| 10h15 a 19h15, de hora em hora | Confere se alguma posicao bateu +10% e se algum swing caiu 20% | So se acontecer |
| 16h15 | Varredura: swings, segunda metade, saidas, trocas e revisoes | So se houver ordem |
| 18h15 | Fechamento: mapa do dia + vencedores e perdedores + sua carteira | Sempre (2 mensagens) |

Fim de semana e feriado americano: nada.

## Regras

- **+10% numa posicao:** vende o excesso acima de US$ 1.000 e manda para a reserva
- **Swing:** candidato 2 ATR ou mais abaixo da EMA 20, nota 6+. Nota 8+ = ate US$ 500; 6 a 7,9 = ate US$ 250. Metade na queda, metade na confirmacao. Sai ao cruzar a EMA 20 ou em 20 pregoes. Maximo de 2 abertos
- **Troca:** so em posicao acima de US$ 1.000, com pelo menos 10 pregoes, quando outro ativo tiver grafico 7+ e 2 pontos acima. Maximo de 2 por semana
- **Revisao:** posicao abaixo de US$ 750 com grafico 3 ou menos. O bot nao vende, so avisa

## Comandos no Telegram

Respondidos na proxima execucao (ate 1 hora, em dias uteis).

| Comando | Faz |
|---|---|
| `/carteira` | Mostra a carteira atual |
| Botao **❌ Recusar** | Aparece em toda sugestao de entrada (swing, 2ª metade, troca). Desfaz a entrada, apaga a mensagem e nao sugere o ativo por 5 dias |
| `/recusar NVDA` | Mesmo efeito do botao, digitando |
| `/nao` | Desfaz a ultima ordem e nao repete no mesmo dia |
| `/reserva 1200` | Corrige o saldo da reserva |
| `/unidades CRWV 12,5` | Corrige a quantidade de um ativo |

## Arquivos

- `bot.py` - o bot
- `carteira.json` - seus ativos. Pode editar a mao
- `estado_bot.json` e `placar_notas.csv` - o bot cria e atualiza sozinho
