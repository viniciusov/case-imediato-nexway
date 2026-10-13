# Resultado da simulação

## Esperado x observado: fim do dia

| Verificação | Esperado | Sem camada | Com camada |
|---|---|---|---|
| Reenvio não gera 2ª fatura (ENT-101) | 1 fatura | FALHA: 2 | OK: 1 |
| ERP fora do ar não perde a fatura (ENT-102) | 1 fatura | FALHA: 0 | OK: 1 |
| Fora de ordem não regride o status (ENT-102) | ENTREGUE | FALHA: EM_TRANSITO | OK: ENTREGUE |
| Inválido vai para quarentena (ENT-103) | 1 em quarentena | FALHA: 0 em quarentena | OK: 1 em quarentena |
| Faturas certas em todas as entregas | 1 / 1 / 0 | FALHA: 2 / 0 / 0 | OK: 1 / 1 / 0 |

## Esperado x observado: após replay

| Verificação | Esperado | Sem camada | Com camada |
|---|---|---|---|
| Reenvio não gera 2ª fatura (ENT-101) | 1 fatura | FALHA: 4 | OK: 1 |
| ERP fora do ar não perde a fatura (ENT-102) | 1 fatura | OK: 1 | OK: 1 |
| Fora de ordem não regride o status (ENT-102) | ENTREGUE | FALHA: EM_TRANSITO | OK: ENTREGUE |
| Inválido vai para quarentena (ENT-103) | 1 em quarentena | FALHA: 0 em quarentena | OK: 1 em quarentena |
| Faturas certas em todas as entregas | 1 / 1 / 0 | FALHA: 4 / 1 / 0 | OK: 1 / 1 / 0 |

## Passo a passo: Camada de integração (proposta)

| Fase | Passo | Entrega | Tipo | Decisão | Efeito |
|---|---|---|---|---|---|
| dia | 1 | ENT-101 | COLETADO | aceito | status = COLETADO |
| dia | 2 | ENT-101 | EM_TRANSITO | aceito | status = EM_TRANSITO |
| dia | 3 | ENT-101 | ENTREGUE | aceito | status = ENTREGUE; enviado à fila do ERP |
| dia | - | ENT-101 | ADAPTADOR_ERP | entregue | fatura criada |
| dia | 4 | ENT-101 | ENTREGUE | duplicata descartada | chave 4956effc84 já vista |
| dia | 5 | ENT-102 | COLETADO | aceito | status = COLETADO |
| dia | 6 | ENT-102 | ENTREGUE | aceito | status = ENTREGUE; enviado à fila do ERP |
| dia | - | ENT-102 | ADAPTADOR_ERP | aguardando | ERP fora do ar: mensagem continua na fila |
| dia | 7 | ENT-102 | EM_TRANSITO | aceito | status mantido (ENTREGUE); evento antigo só no histórico |
| dia | 8 | ENT-103 | ENTREGEU | quarentena | tipo desconhecido ou sem horário |
| dia | - | ENT-102 | ADAPTADOR_ERP | entregue | fatura criada |
| dia | 9 | ENT-103 | COLETADO | aceito | status = COLETADO |
| replay | 1 | ENT-101 | COLETADO | aceito | status mantido (ENTREGUE); evento antigo só no histórico |
| replay | 2 | ENT-101 | EM_TRANSITO | aceito | status mantido (ENTREGUE); evento antigo só no histórico |
| replay | 3 | ENT-101 | ENTREGUE | aceito | status mantido (ENTREGUE); evento antigo só no histórico; enviado à fila do ERP |
| replay | - | ENT-101 | ADAPTADOR_ERP | entregue | fatura com esta referência já existe: nada criado |
| replay | 4 | ENT-101 | ENTREGUE | aceito | status mantido (ENTREGUE); evento antigo só no histórico; enviado à fila do ERP |
| replay | - | ENT-101 | ADAPTADOR_ERP | entregue | fatura com esta referência já existe: nada criado |
| replay | 5 | ENT-102 | COLETADO | aceito | status mantido (ENTREGUE); evento antigo só no histórico |
| replay | 6 | ENT-102 | ENTREGUE | aceito | status mantido (ENTREGUE); evento antigo só no histórico; enviado à fila do ERP |
| replay | - | ENT-102 | ADAPTADOR_ERP | entregue | fatura com esta referência já existe: nada criado |
| replay | 7 | ENT-102 | EM_TRANSITO | aceito | status mantido (ENTREGUE); evento antigo só no histórico |
| replay | 8 | ENT-103 | ENTREGEU | quarentena | continua em quarentena (não duplica o registro) |
| replay | 9 | ENT-103 | COLETADO | aceito | status mantido (COLETADO); evento antigo só no histórico |

## Passo a passo: Integração direta (sem camada)

| Fase | Passo | Entrega | Tipo | Decisão | Efeito |
|---|---|---|---|---|---|
| dia | 1 | ENT-101 | COLETADO | aplicado | status = COLETADO |
| dia | 2 | ENT-101 | EM_TRANSITO | aplicado | status = EM_TRANSITO |
| dia | 3 | ENT-101 | ENTREGUE | aplicado | status = ENTREGUE; fatura criada |
| dia | 4 | ENT-101 | ENTREGUE | aplicado | status = ENTREGUE; fatura criada |
| dia | 5 | ENT-102 | COLETADO | aplicado | status = COLETADO |
| dia | 6 | ENT-102 | ENTREGUE | aplicado | status = ENTREGUE; ERP fora do ar: fatura PERDIDA |
| dia | 7 | ENT-102 | EM_TRANSITO | aplicado | status = EM_TRANSITO |
| dia | 8 | ENT-103 | ENTREGEU | aplicado | status = ENTREGEU |
| dia | 9 | ENT-103 | COLETADO | aplicado | status = COLETADO |
| replay | 1 | ENT-101 | COLETADO | aplicado | status = COLETADO |
| replay | 2 | ENT-101 | EM_TRANSITO | aplicado | status = EM_TRANSITO |
| replay | 3 | ENT-101 | ENTREGUE | aplicado | status = ENTREGUE; fatura criada |
| replay | 4 | ENT-101 | ENTREGUE | aplicado | status = ENTREGUE; fatura criada |
| replay | 5 | ENT-102 | COLETADO | aplicado | status = COLETADO |
| replay | 6 | ENT-102 | ENTREGUE | aplicado | status = ENTREGUE; fatura criada |
| replay | 7 | ENT-102 | EM_TRANSITO | aplicado | status = EM_TRANSITO |
| replay | 8 | ENT-103 | ENTREGEU | aplicado | status = ENTREGEU |
| replay | 9 | ENT-103 | COLETADO | aplicado | status = COLETADO |

