"""
Simulação: idempotência na integração de eventos de transporte
================================================================

Hipótese testada
----------------
Reenvios, eventos fora de ordem, indisponibilidade do ERP e reprocessamento
NÃO geram faturas duplicadas, NÃO perdem faturas e NÃO regridem o status de
uma entrega, desde que os eventos passem pela camada de integração proposta
(chave de idempotência + máquina de estados + fila com nova tentativa +
verificação de referência no ERP).

Como funciona o experimento
---------------------------
A mesma sequência de eventos fictícios é processada duas vezes:

  1. Integração direta (grupo de controle): cada evento vai direto ao destino,
     sem validação, sem deduplicação e sem fila. É o modelo ponto a ponto.
  2. Camada de integração (proposta): o evento é validado, recebe uma chave de
     idempotência, passa pela máquina de estados e vai para uma fila; o
     adaptador do ERP consulta a referência antes de criar a fatura.

Depois do dia "normal", os dois modelos fazem um reprocessamento completo
(replay) de todos os eventos. No fim, o script compara o resultado esperado
com o observado em cada modelo.

Tudo é simulado em memória: não há banco, fila nem SAP de verdade.
Requer apenas Python 3.8+ (biblioteca padrão).

Como rodar
----------
    python simulacao_idempotencia.py              # resumo + passo a passo da camada
    python simulacao_idempotencia.py --detalhado  # inclui o passo a passo do controle
    python simulacao_idempotencia.py --sem-verificacao-erp
        # desliga a consulta ao ERP antes de gravar: mostra que, sem essa segunda
        # defesa, o reprocessamento volta a duplicar faturas
"""

import hashlib
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 1. Regras do domínio
# ---------------------------------------------------------------------------

# Ordem válida dos status de uma entrega (máquina de estados).
# Um evento só altera o status se representar uma etapa POSTERIOR à atual.
ORDEM_STATUS = {
    "COLETADO": 1,
    "EM_TRANSITO": 2,
    "SAIU_PARA_ENTREGA": 3,
    "ENTREGUE": 4,
}

# Só a entrega concluída gera efeito no ERP (premissa do documento:
# o ERP recebe apenas eventos com efeito de faturamento).
EVENTO_QUE_FATURA = "ENTREGUE"


@dataclass
class Evento:
    passo: int
    entrega: str
    tipo: str
    ocorreu_em: str        # horário da OCORRÊNCIA (event time), "HH:MM"
    descricao: str
    erp_disponivel: bool = True   # estado do ERP quando o evento é processado


# ---------------------------------------------------------------------------
# 2. Entrada do experimento (dados fictícios)
# ---------------------------------------------------------------------------

ENTRADA = [
    Evento(1, "ENT-101", "COLETADO",    "08:00", "Coleta da entrega 101"),
    Evento(2, "ENT-101", "EM_TRANSITO", "09:10", "Entrega 101 em trânsito"),
    Evento(3, "ENT-101", "ENTREGUE",    "10:05", "Entrega 101 concluída"),
    Evento(4, "ENT-101", "ENTREGUE",    "10:05",
           "REENVIO do mesmo evento após a unidade reconectar (duplicata)"),
    Evento(5, "ENT-102", "COLETADO",    "08:30", "Coleta da entrega 102"),
    Evento(6, "ENT-102", "ENTREGUE",    "11:20",
           "Entrega 102 concluída, mas o ERP está FORA DO AR", erp_disponivel=False),
    Evento(7, "ENT-102", "EM_TRANSITO", "09:40",
           "Evento ATRASADO: 'em trânsito' chega depois de 'entregue'", erp_disponivel=False),
    Evento(8, "ENT-103", "ENTREGEU",    "",
           "Evento INVÁLIDO: tipo desconhecido e sem horário (ERP volta ao ar)"),
    Evento(9, "ENT-103", "COLETADO",    "10:00", "Coleta da entrega 103"),
]

# Resultado esperado, segundo a hipótese (vale para o fim do dia E depois do replay)
ESPERADO = {
    "faturas": {"ENT-101": 1, "ENT-102": 1, "ENT-103": 0},
    "status":  {"ENT-101": "ENTREGUE", "ENT-102": "ENTREGUE", "ENT-103": "COLETADO"},
    "quarentena": 1,
}


# ---------------------------------------------------------------------------
# 3. ERP falso (representa o SAP Business One via Service Layer)
# ---------------------------------------------------------------------------

class ERPIndisponivel(Exception):
    pass


class ERPFalso:
    """Cria uma fatura a cada chamada. Não sabe nada sobre duplicidade."""

    def __init__(self):
        self.faturas = []          # cada fatura: {"entrega":..., "referencia":...}
        self.disponivel = True

    def criar_fatura(self, entrega, referencia=None):
        if not self.disponivel:
            raise ERPIndisponivel()
        self.faturas.append({"entrega": entrega, "referencia": referencia})

    def existe_fatura(self, referencia):
        # Equivale a consultar o campo de referência externa (UDF) da fatura
        if not self.disponivel:
            raise ERPIndisponivel()
        return any(f["referencia"] == referencia for f in self.faturas)

    def total_de(self, entrega):
        return sum(1 for f in self.faturas if f["entrega"] == entrega)


# ---------------------------------------------------------------------------
# 4. Grupo de controle: integração direta, sem camada
# ---------------------------------------------------------------------------

class IntegracaoDireta:
    nome = "Integração direta (sem camada)"

    def __init__(self):
        self.erp = ERPFalso()
        self.status = {}
        self.quarentena = []
        self.log = []

    def receber(self, ev, fase):
        self.erp.disponivel = ev.erp_disponivel
        self.status[ev.entrega] = ev.tipo              # o último a chegar vence
        efeito = f"status = {ev.tipo}"
        if ev.tipo == EVENTO_QUE_FATURA:
            try:
                self.erp.criar_fatura(ev.entrega)
                efeito += "; fatura criada"
            except ERPIndisponivel:
                efeito += "; ERP fora do ar: fatura PERDIDA"
        self.log.append((fase, ev.passo, ev.entrega, ev.tipo, "aplicado", efeito))

    def reprocessar(self, eventos):
        for ev in eventos:
            ev_replay = Evento(ev.passo, ev.entrega, ev.tipo, ev.ocorreu_em, ev.descricao, True)
            self.receber(ev_replay, "replay")


# ---------------------------------------------------------------------------
# 5. Proposta: camada de integração
# ---------------------------------------------------------------------------

class CamadaIntegracao:
    nome = "Camada de integração (proposta)"
    verifica_no_erp = True       # desligue com --sem-verificacao-erp para ver o efeito

    def __init__(self):
        self.erp = ERPFalso()
        self.status = {}             # estado atual de cada entrega
        self.historico = []          # todos os eventos aceitos, inclusive antigos
        self.inbox = set()           # chaves já processadas (deduplicação)
        self.event_store = []        # eventos originais guardados para replay
        self.fila_erp = []           # mensagens aguardando o adaptador do ERP
        self.quarentena = []
        self.log = []

    # -- utilidades --------------------------------------------------------
    @staticmethod
    def chave(ev):
        """Chave determinística: mesmo fato -> mesma chave, venha de onde vier."""
        base = f"{ev.entrega}|{ev.tipo}|{ev.ocorreu_em}"
        return hashlib.sha256(base.encode()).hexdigest()[:10]

    @staticmethod
    def valido(ev):
        return ev.tipo in ORDEM_STATUS and bool(ev.ocorreu_em)

    def registrar(self, fase, ev, decisao, efeito):
        self.log.append((fase, ev.passo, ev.entrega, ev.tipo, decisao, efeito))

    # -- fluxo principal ---------------------------------------------------
    def receber(self, ev, fase="dia", reprocesso=False):
        self.erp.disponivel = ev.erp_disponivel
        if not reprocesso:
            self.event_store.append(ev)                 # guarda o original

        # 1) Validação: inválido vai para quarentena, sem efeito
        if not self.valido(ev):
            if reprocesso and any(q.passo == ev.passo for q in self.quarentena):
                self.registrar(fase, ev, "quarentena", "continua em quarentena (não duplica o registro)")
            else:
                self.quarentena.append(ev)
                self.registrar(fase, ev, "quarentena", "tipo desconhecido ou sem horário")
            self.entregar_ao_erp(fase)
            return

        chave = self.chave(ev)

        # 2) Deduplicação na entrada (inbox). No reprocessamento ela é
        #    contornada de propósito, para provar as outras defesas.
        if not reprocesso:
            if chave in self.inbox:
                self.registrar(fase, ev, "duplicata descartada", f"chave {chave} já vista")
                self.entregar_ao_erp(fase)
                return
            self.inbox.add(chave)

        # 3) Máquina de estados: evento antigo não regride o status
        atual = self.status.get(ev.entrega)
        self.historico.append((ev.entrega, ev.tipo, ev.ocorreu_em))
        if atual and ORDEM_STATUS[ev.tipo] <= ORDEM_STATUS[atual]:
            efeito = f"status mantido ({atual}); evento antigo só no histórico"
        else:
            self.status[ev.entrega] = ev.tipo
            efeito = f"status = {ev.tipo}"

        # 4) Roteamento: só o evento de faturamento segue para o ERP
        if ev.tipo == EVENTO_QUE_FATURA:
            self.fila_erp.append({"entrega": ev.entrega, "referencia": chave})
            efeito += "; enviado à fila do ERP"
        self.registrar(fase, ev, "aceito", efeito)

        self.entregar_ao_erp(fase)

    def entregar_ao_erp(self, fase):
        """Adaptador do ERP: consulta antes de gravar e tenta de novo depois."""
        pendentes = []
        for msg in self.fila_erp:
            try:
                if self.verifica_no_erp and self.erp.existe_fatura(msg["referencia"]):
                    acao = "fatura com esta referência já existe: nada criado"
                else:
                    self.erp.criar_fatura(msg["entrega"], msg["referencia"])
                    acao = "fatura criada"
                self.log.append((fase, "-", msg["entrega"], "ADAPTADOR_ERP", "entregue", acao))
            except ERPIndisponivel:
                pendentes.append(msg)          # continua na fila, sem perda
        if pendentes and pendentes != getattr(self, "_avisado", None):
            entregas = ", ".join(m["entrega"] for m in pendentes)
            self.log.append((fase, "-", entregas, "ADAPTADOR_ERP", "aguardando",
                             "ERP fora do ar: mensagem continua na fila"))
            self._avisado = list(pendentes)
        self.fila_erp = pendentes

    def reprocessar(self, _eventos_ignorados=None):
        """Replay a partir do event store, com o ERP disponível."""
        for ev in list(self.event_store):
            ev_replay = Evento(ev.passo, ev.entrega, ev.tipo, ev.ocorreu_em, ev.descricao, True)
            self.receber(ev_replay, "replay", reprocesso=True)


# ---------------------------------------------------------------------------
# 6. Verificações (esperado x observado)
# ---------------------------------------------------------------------------

def verificar(modelo):
    """Devolve (verificação, esperado, observado, atendeu?) para o estado atual."""
    erp, st = modelo.erp, modelo.status
    f = {e: erp.total_de(e) for e in ESPERADO["faturas"]}
    certas = all(f[e] == n for e, n in ESPERADO["faturas"].items())
    return [
        ("Reenvio não gera 2ª fatura (ENT-101)", "1 fatura",
         str(f["ENT-101"]), f["ENT-101"] == 1),
        ("ERP fora do ar não perde a fatura (ENT-102)", "1 fatura",
         str(f["ENT-102"]), f["ENT-102"] == 1),
        ("Fora de ordem não regride o status (ENT-102)", "ENTREGUE",
         str(st.get("ENT-102")), st.get("ENT-102") == "ENTREGUE"),
        ("Inválido vai para quarentena (ENT-103)", "1 em quarentena",
         f"{len(modelo.quarentena)} em quarentena", len(modelo.quarentena) == ESPERADO["quarentena"] and f["ENT-103"] == 0),
        ("Faturas certas em todas as entregas", "1 / 1 / 0",
         f"{f['ENT-101']} / {f['ENT-102']} / {f['ENT-103']}", certas),
    ]


# ---------------------------------------------------------------------------
# 7. Saída
# ---------------------------------------------------------------------------

def linha(c=96):
    return "-" * c


def imprimir_entrada():
    print("ENTRADA (eventos fictícios, na ordem em que chegam)")
    print(linha())
    print(f"{'#':>2}  {'Entrega':8} {'Tipo':12} {'Ocorreu':7} {'ERP':5}  Situação simulada")
    for ev in ENTRADA:
        erp = "ok" if ev.erp_disponivel else "FORA"
        print(f"{ev.passo:>2}  {ev.entrega:8} {ev.tipo:12} {ev.ocorreu_em or '--:--':7} {erp:5}  {ev.descricao}")
    print("Depois do dia: REPROCESSAMENTO completo de todos os eventos (replay), com o ERP no ar.")
    print()


def imprimir_log(modelo):
    print(f"PASSO A PASSO: {modelo.nome}")
    print(linha())
    for fase, passo, entrega, tipo, decisao, efeito in modelo.log:
        print(f"[{fase:6}] {str(passo):>2}  {entrega:16} {tipo:14} {decisao:21} {efeito}")
    print()


def imprimir_resultado(resultados):
    print("RESULTADO: ESPERADO x OBSERVADO")
    larg = (45, 16, 24, 24)
    cab = f"{'Verificação':{larg[0]}} {'Esperado':{larg[1]}} {'Sem camada':{larg[2]}} {'Com camada':{larg[3]}}"
    total = {"c": 0, "p": 0, "n": 0}
    for momento in ("fim do dia", "após replay"):
        print(linha(len(cab)))
        print(f"Momento: {momento}")
        print(cab)
        print(linha(len(cab)))
        for (nome, esp, obs_c, ok_c), (_, _, obs_p, ok_p) in zip(resultados[("c", momento)], resultados[("p", momento)]):
            rc = ("OK: " if ok_c else "FALHA: ") + obs_c
            rp = ("OK: " if ok_p else "FALHA: ") + obs_p
            print(f"{nome:{larg[0]}} {esp:{larg[1]}} {rc:{larg[2]}} {rp:{larg[3]}}")
            total["c"] += ok_c; total["p"] += ok_p; total["n"] += 1
    print(linha(len(cab)))
    print(f"Verificações atendidas: sem camada {total['c']}/{total['n']}  |  com camada {total['p']}/{total['n']}")
    print()


def salvar_markdown(controle, proposta, resultados, caminho="resultado_simulacao.md"):
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("# Resultado da simulação\n\n")
        for momento in ("fim do dia", "após replay"):
            f.write(f"## Esperado x observado: {momento}\n\n")
            f.write("| Verificação | Esperado | Sem camada | Com camada |\n|---|---|---|---|\n")
            for (nome, esp, obs_c, ok_c), (_, _, obs_p, ok_p) in zip(resultados[("c", momento)], resultados[("p", momento)]):
                f.write(f"| {nome} | {esp} | {'OK' if ok_c else 'FALHA'}: {obs_c} | {'OK' if ok_p else 'FALHA'}: {obs_p} |\n")
            f.write("\n")
        for m in (proposta, controle):
            f.write(f"## Passo a passo: {m.nome}\n\n| Fase | Passo | Entrega | Tipo | Decisão | Efeito |\n|---|---|---|---|---|---|\n")
            for fase, passo, entrega, tipo, decisao, efeito in m.log:
                f.write(f"| {fase} | {passo} | {entrega} | {tipo} | {decisao} | {efeito} |\n")
            f.write("\n")
    return caminho


def executar(detalhado=False, sem_verificacao_erp=False):
    CamadaIntegracao.verifica_no_erp = not sem_verificacao_erp
    CamadaIntegracao.nome = ("Camada de integração SEM a verificação no ERP (teste de defesa em profundidade)"
                             if sem_verificacao_erp else "Camada de integração (proposta)")
    print()
    print("SIMULAÇÃO: idempotência na integração de eventos de transporte")
    print("Hipótese: reenvio, atraso, ERP fora do ar e replay não duplicam nem perdem faturas")
    print("e não regridem o status de uma entrega.")
    print()
    imprimir_entrada()

    controle, proposta = IntegracaoDireta(), CamadaIntegracao()
    resultados = {}
    for rotulo, modelo in (("c", controle), ("p", proposta)):
        for ev in ENTRADA:
            modelo.receber(ev, "dia")
        resultados[(rotulo, "fim do dia")] = verificar(modelo)
        modelo.reprocessar(ENTRADA)
        resultados[(rotulo, "após replay")] = verificar(modelo)

    if detalhado:
        imprimir_log(controle)
    imprimir_log(proposta)
    imprimir_resultado(resultados)
    arquivo = salvar_markdown(controle, proposta, resultados)
    print(f"Resultado também salvo em: {arquivo}")
    print()
    return resultados


if __name__ == "__main__":
    executar(detalhado="--detalhado" in sys.argv,
             sem_verificacao_erp="--sem-verificacao-erp" in sys.argv)
