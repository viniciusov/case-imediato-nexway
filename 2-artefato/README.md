# Artefato: simulação de idempotência na integração de eventos de transporte

Experimento pequeno que testa a hipótese mais crítica da arquitetura recomendada em [`1-analise-e-decisao`](../1-analise-e-decisao). Todos os dados são fictícios.

## Hipótese

Reenvios, eventos fora de ordem, indisponibilidade do ERP e reprocessamento **não** geram faturas duplicadas, **não** perdem faturas e **não** regridem o status de uma entrega, desde que os eventos passem pela camada de integração proposta.

Escolhemos esta hipótese porque ela sustenta toda a arquitetura: a entrega "pelo menos uma vez" só é segura se o processamento for idempotente. Se ela falhar, a proposta gera faturas em dobro, que é o pior efeito para o negócio.

## O que o experimento faz

A mesma sequência de eventos fictícios é processada em dois modelos:

- **Integração direta (grupo de controle):** cada evento vai direto ao destino, sem validação, deduplicação ou fila. Representa o modelo ponto a ponto atual.
- **Camada de integração (proposta):** valida o evento, gera a chave de idempotência, aplica a máquina de estados, coloca na fila e o adaptador do ERP consulta a referência antes de criar a fatura.

Depois do dia, os dois modelos fazem um reprocessamento completo (replay) de todos os eventos. Por fim, o script compara o esperado com o observado.

| Componente da proposta | Onde está no código |
|---|---|
| Validação e quarentena | `CamadaIntegracao.valido` e a lista `quarentena` |
| Chave de idempotência (entrega + tipo + horário da ocorrência) | `CamadaIntegracao.chave` |
| Deduplicação na entrada (inbox) | conjunto `inbox` |
| Máquina de estados | dicionário `ORDEM_STATUS` |
| Event store e replay | lista `event_store` e método `reprocessar` |
| Fila com nova tentativa | lista `fila_erp` |
| Adaptador do ERP com verificação de referência | método `entregar_ao_erp` |
| ERP (SAP B1 via Service Layer) | classe `ERPFalso` |

## Entrada

| # | Entrega | Evento | Ocorreu | ERP | Situação simulada |
|---|---|---|---|---|---|
| 1 | ENT-101 | COLETADO | 08:00 | no ar | Coleta |
| 2 | ENT-101 | EM_TRANSITO | 09:10 | no ar | Em trânsito |
| 3 | ENT-101 | ENTREGUE | 10:05 | no ar | Entrega concluída |
| 4 | ENT-101 | ENTREGUE | 10:05 | no ar | Reenvio do mesmo evento após a unidade reconectar |
| 5 | ENT-102 | COLETADO | 08:30 | no ar | Coleta |
| 6 | ENT-102 | ENTREGUE | 11:20 | fora | Entrega concluída com o ERP fora do ar |
| 7 | ENT-102 | EM_TRANSITO | 09:40 | fora | Evento atrasado, chega depois de "entregue" |
| 8 | ENT-103 | ENTREGEU | (sem) | no ar | Evento inválido; o ERP volta ao ar |
| 9 | ENT-103 | COLETADO | 10:00 | no ar | Coleta |

Depois: reprocessamento completo de todos os eventos, com o ERP no ar.

## Resultado esperado

No fim do dia e depois do reprocessamento: uma fatura para ENT-101, uma para ENT-102 e nenhuma para ENT-103; status ENT-101 e ENT-102 = ENTREGUE e ENT-103 = COLETADO; um evento em quarentena.

## Resultado observado

| Momento | Verificação | Esperado | Sem camada | Com camada |
|---|---|---|---|---|
| Fim do dia | Reenvio não gera 2ª fatura (ENT-101) | 1 fatura | FALHA: 2 | OK: 1 |
| Fim do dia | ERP fora do ar não perde a fatura (ENT-102) | 1 fatura | FALHA: 0 | OK: 1 |
| Fim do dia | Fora de ordem não regride o status (ENT-102) | ENTREGUE | FALHA: EM_TRANSITO | OK: ENTREGUE |
| Fim do dia | Inválido vai para quarentena (ENT-103) | 1 em quarentena | FALHA: 0 | OK: 1 |
| Fim do dia | Faturas certas em todas as entregas | 1 / 1 / 0 | FALHA: 2 / 0 / 0 | OK: 1 / 1 / 0 |
| Após replay | Reenvio não gera 2ª fatura (ENT-101) | 1 fatura | FALHA: 4 | OK: 1 |
| Após replay | ERP fora do ar não perde a fatura (ENT-102) | 1 fatura | OK: 1 | OK: 1 |
| Após replay | Fora de ordem não regride o status (ENT-102) | ENTREGUE | FALHA: EM_TRANSITO | OK: ENTREGUE |
| Após replay | Inválido vai para quarentena (ENT-103) | 1 em quarentena | FALHA: 0 | OK: 1 |
| Após replay | Faturas certas em todas as entregas | 1 / 1 / 0 | FALHA: 4 / 1 / 0 | OK: 1 / 1 / 0 |

**Total: sem camada 1 de 10 verificações; com camada 10 de 10.** A hipótese se confirmou na simulação.

## O que o resultado mostra

- **Cada defesa resolve um problema específico.** A deduplicação barrou o reenvio; a fila segurou a fatura enquanto o ERP estava fora; a máquina de estados impediu a regressão de status; a validação isolou o evento inválido.
- **Reprocessar sem idempotência piora o problema.** No controle, o replay recupera a fatura perdida da ENT-102, mas duplica de novo as da ENT-101 (de 2 para 4). Reprocessamento só é seguro com idempotência.
- **A verificação no ERP é necessária, não redundante.** Rodando com `--sem-verificacao-erp`, o fim do dia continua correto (a deduplicação protege), mas o replay volta a duplicar faturas (7 de 10). Isso justifica a defesa em profundidade proposta no documento.

## Limitações

- Simulação em memória: não há fila, banco nem SAP B1 reais, e não há concorrência. A gravação atômica da chave (por exemplo, restrição de unicidade no banco) não está sendo testada aqui.
- Os eventos são fictícios e poucos; não testam volume nem picos de 10x.
- A chave usa entrega + tipo + horário da ocorrência; se o horário real tiver precisão de minutos, dois eventos legítimos podem colidir (risco registrado nas premissas).
- O replay contorna a deduplicação de propósito, para provar as outras defesas; num sistema real, a deduplicação na entrada também teria prazo de retenção.
- A validação real acontece no spike das semanas 1 e 2 do piloto, com o SAP B1 e o TMS de verdade.

## Como rodar

**Google Colab (recomendado, nada a instalar):**

[![Abrir no Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/viniciusov/case-imediato-nexway/blob/main/2-artefato/simulacao_idempotencia.ipynb)

1. Clique no botão acima (é preciso estar logado em uma conta Google).
2. Ambiente de execução → Executar tudo.

**Terminal (Python 3.8 ou superior, sem bibliotecas extras):**

```
python simulacao_idempotencia.py                       # resumo + passo a passo da camada
python simulacao_idempotencia.py --detalhado           # inclui o passo a passo do controle
python simulacao_idempotencia.py --sem-verificacao-erp # teste de defesa em profundidade
```

O script também grava `resultado_simulacao.md` com as tabelas e o passo a passo.

## Uso de IA nesta etapa

1. **Problema:** preparar um experimento pequeno, reproduzível e legível para testar a hipótese de idempotência, cobrindo as falhas listadas no case.
2. **Entradas:** o enunciado do case, as premissas e decisões do documento principal (chave determinística, máquina de estados, ERP passivo, fila com nova tentativa) e a lista de falhas a cobrir. Apenas dados fictícios.
3. **Instrução ou fluxo:** pedi à IA (Claude) um script em Python sem dependências que processasse a mesma sequência de eventos em dois modelos, com e sem a camada, e comparasse esperado e observado. Os cenários foram definidos a partir das seis situações do case.
4. **Resultado:** o script, a versão para Colab e este guia.
5. **Verificação:** executei o script e conferi cada linha do passo a passo contra o resultado esperado; confirmei que o grupo de controle falha onde deveria (se tudo passasse nos dois modelos, o teste não provaria nada); rodei a variante sem a verificação no ERP para garantir que as verificações detectam uma defesa removida; executei duas vezes para confirmar que o resultado é sempre o mesmo.
6. **Reuso pela equipe:** os eventos de entrada e o resultado esperado ficam no topo do script; para testar outro cenário, basta acrescentar linhas em `ENTRADA` e ajustar `ESPERADO`. O mesmo formato serve como base para os testes de aceite do piloto.
