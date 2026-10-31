"""Modelo stock: ativo + put delta -0.30 com entrada no dia do sinal (oper_abertas, dia_hist = 1).

Antes Evolucao_put_30.py (e antes Evolucao_collar.py); tabela Evolucao_stock (antes Evolucao_put_30).
Na view Evolucao_put_operacoes so aparece o modelo stock (so o ativo, com as saidas desta simulacao);
tambem gera Mercado_volatilidade. Funcoes comuns de opcoes: Opcoes_base.py.

Mesma estrutura de colunas do Evolucao_opc_collar, sem etapa 2.

Metodo atual: sinais ext + fase2 (ESTRATEGIA = None) com ordem_selecao_dia <= ORDEM_MAX (1), put delta -0.30
(PUT_DELTA), alvo +10%, stop -4% no ativo (STOP_PCT), sem corte: fica ate ao vencimento da put (CORTE_DIAS = None). A coluna estrategia mostra a origem (ext, fase2 ou ext+fase2).

Sinais   oper_abertas com estrategia = ESTRATEGIA e ordem_selecao_dia <= ORDEM_MAX, com opcoes liquidas.
Entrada  no dia da operacao, ao close (close_oper):
           - compra a put com o maior strike <= close_oper * (1 - PUT_DIST) (PUT_DIST = None: put com delta mais proximo de PUT_DELTA);
           - compra o ativo;
           - opcional: vende a call com delta mais proximo de CALL_DELTA (None = sem call);
         no 1.o vencimento aceite por base.venc_valido (sexta com >= 10 du). Opcoes do registo desse dia
         (ou do mais recente ate DIAS_FALLBACK_CADEIA dias antes).
Stop     1.o dia apos a operacao em que o Min do ativo <= close_oper * (1 - STOP_PCT): liquida tudo (vende o ativo
         ao stop ou na abertura, se abrir abaixo; vende a put) (status 'stop'). Alvo e stop no mesmo dia: conta o stop.
         STOP_PCT = None: sem stop.
Saida    1.o dia apos a operacao, ate ao D+CORTE_DIAS, em que o Max do ativo >= close_oper * (1 + ALVO_PCT):
         liquida tudo (vende o ativo ao alvo ou na abertura, se abrir acima; recompra a call; vende a put).
         Sem alvo ate ao D+CORTE_DIAS: liquida ao close desse dia (status 'corte').
         CORTE_DIAS = None: sem corte, liquida no vencimento. Sem rolagem.
Trava    entrada das opcoes dos sinais de hoje em Entrada_opc_travada (modelo stock), ver Opcoes_base.
Precos   opcoes a Black-Scholes (formula de BS precificacao.xlsx) com o preco do ativo no momento
         e a volatilidade implicita da recolha do dia; sem volatilidade usa o lastPrice.
Sinais   entrada_* e saida_* seguem o Evolucao_opc_collar: o que se paga e negativo, o que se recebe e positivo.
"""
import sqlite3
from datetime import timedelta

import duckdb
import numpy as np
import pandas as pd

import Opcoes_base as base
from Evolucao_opc_collar import bs_opcao
from Opcoes_base import (DIAS_FALLBACK_CADEIA, SQLITE_FILE, TABELA_TRAVAS, avaliar_com_travas, cadeia, carregar_opcoes,
                         carregar_operacoes, carregar_precos, dias_uteis, escolher_por_delta, escolher_put_por_distancia,
                         para_data, preco_opcao, ymd)


DB_FILE = base.DB_FILE
TABELA_DESTINO = "Evolucao_stock"
MODELO = "stock"
TABELA_VOLATILIDADE = "Mercado_volatilidade"
DESDE_VOLATILIDADE = "20250101"
VOL_CALMO = 45      # vol mediana dos tickers (% ao ano) abaixo disto: mercado calmo
VOL_AGITADO = 55    # acima disto: mercado agitado

ESTRATEGIA = None    # None = ext + fase2; "ext" ou "fase2" para so uma estrategia
ORDEM_MAX = 1        # so sinais com ordem_selecao_dia <= ORDEM_MAX (None = todos)
CORTE_DIAS = None    # None = sem alvo, fica ate ao vencimento da put; ex.: 3 = liquida ao close do D+3

PUT_DIST = None   # None = put com delta mais proximo de PUT_DELTA; ex.: 0.03 = maior strike ate 3% abaixo do preco
PUT_DELTA = -0.30
CALL_DELTA = None  # None = nao vende call (ativo + put); ex.: 0.18 para vender call (collar)
ALVO_PCT = 0.10
STOP_PCT = 0.04  # stop no ativo: Min <= close_oper * (1 - STOP_PCT) liquida tudo (None = sem stop)


def escolher_entrada(op, opc_t, C, d0):
    """Pernas de entrada pela cadeia do dia; na falha devolve {'status': ...} (com venc quando ja escolhido)."""
    puts = cadeia(opc_t, "PUT", op.dataoperacao)
    if puts.empty:
        return {"status": "sem_opcoes"}
    vencs = sorted(v for v in puts.venc.unique() if base.venc_valido(d0, v))
    if not vencs:
        return {"status": "sem_vencimento"}
    venc = vencs[0]
    du0 = dias_uteis(d0, venc)
    if PUT_DIST:
        p = escolher_put_por_distancia(puts[puts.venc == venc], C, du0, PUT_DIST)
    else:
        p = escolher_por_delta(puts[puts.venc == venc], C, du0, PUT_DELTA, "PUT")
    if p is None:
        return {"status": "sem_put", "venc_put": venc}
    kp, iv_p = float(p.strike), float(p.iv)
    if iv_p > 5:  # linhas inseridas manualmente com a IV em % (80.0 em vez de 0.80)
        iv_p = iv_p / 100
    put0 = bs_opcao(C, kp, op.dataoperacao, venc, iv_p, "PUT") or float(p.lp)
    e = dict(venc_put=venc, strike_put=kp, iv_put=iv_p, valor_put=put0, bid_put=p.bid, ask_put=p.ask)
    if CALL_DELTA:
        c = escolher_por_delta(cadeia(opc_t, "CALL", op.dataoperacao, venc), C, du0, CALL_DELTA, "CALL")
        if c is None:
            return {"status": "sem_call", "venc_put": venc}
        kc, iv_c = float(c.strike), float(c.iv)
        call0 = bs_opcao(C, kc, op.dataoperacao, venc, iv_c, "CALL") or float(c.lp)
        e.update(venc_call=venc, strike_call=kc, iv_call=iv_c, valor_call=call0, bid_call=c.bid, ask_call=c.ask)
    return e


def avaliar(op, px_t, opc_t, trava=None):
    """trava: entrada ja gravada em TABELA_TRAVAS para este sinal (usa-a em vez de escolher pela cadeia do dia)."""
    out = {"dataoperacao": op.dataoperacao, "hora": op.hora, "ticker": op.ticker, "estrategia": op.estrategia, "close_oper": op.close_oper,
           "ordem_selecao_dia": op.ordem_selecao_dia, "rank_mm50_dia": op.rank_mm50_dia, "dist_MM50%": op.dist_mm50}
    if px_t is None or opc_t is None or op.close_oper is None or op.close_oper != op.close_oper:
        out["status"] = "sem_dados"
        return out
    d0 = para_data(op.dataoperacao)
    C = float(op.close_oper)

    # Entrada: put comprada, ativo comprado, call vendida (mesmo dia); sinal travado usa a entrada da 1.a rodada
    e = trava if trava is not None else escolher_entrada(op, opc_t, C, d0)
    if e.get("venc_put"):
        out.update(venc_put=e["venc_put"].isoformat(), dias_venc=dias_uteis(d0, e["venc_put"]))
    if e.get("status"):
        out["status"] = e["status"]
        return out
    out["_entrada"] = e
    venc = e["venc_put"]
    venc_s = ymd(venc)
    kp, iv_p, put0 = e["strike_put"], e["iv_put"], e["valor_put"]
    out.update(strike_put=kp, valor_put=round(put0, 2), bid_put=e["bid_put"], ask_put=e["ask_put"], vol_put=round(iv_p, 2),
               entrada_put=round(-put0, 2), entrada_ativo=round(-C, 2))
    kc = iv_c = None
    call0 = 0.0
    if CALL_DELTA:
        if e.get("strike_call") is None:
            out["status"] = "sem_call"
            return out
        kc, iv_c, call0 = e["strike_call"], e["iv_call"], e["valor_call"]
        out.update(strike_call=kc, valor_call=round(call0, 2), bid_call=e["bid_call"], ask_call=e["ask_call"],
                   vol_call=round(iv_c, 2), entrada_call=round(call0, 2))
    custo = C + put0 - call0

    # Saida: stop (Min <= close_oper * (1 - STOP_PCT)) ou alvo (Max >= close_oper * (1 + ALVO_PCT)), o 1.o que acontecer,
    # a partir do dia seguinte ate ao D+CORTE_DIAS (ou ao vencimento); no mesmo dia conta o stop;
    # sem stop nem alvo: corte ao close do D+CORTE_DIAS, ou liquidacao no vencimento
    alvo = C * (1 + ALVO_PCT)
    nivel_stop = C * (1 - STOP_PCT) if STOP_PCT else None
    out["alvo_saida"] = round(alvo, 2)
    out["stop_saida"] = round(nivel_stop, 2) if nivel_stop else None
    ult = px_t[px_t.index > op.dataoperacao]
    out["varAtualativo"] = round((ult.c.iloc[-1] / C - 1) * 100, 2) if not ult.empty else None
    dias_op = px_t[(px_t.index > op.dataoperacao) & (px_t.index <= venc_s)]
    janela = dias_op.iloc[:CORTE_DIAS] if CORTE_DIAS else dias_op
    toque = janela[janela.h >= alvo]
    stop_hit = janela[janela.l <= nivel_stop] if nivel_stop else janela.iloc[0:0]
    vencida = px_t.index[-1] >= venc_s
    corte_feito = bool(CORTE_DIAS) and len(dias_op) >= CORTE_DIAS
    if not stop_hit.empty and (toque.empty or stop_hit.index[0] <= toque.index[0]):
        d_saida = stop_hit.index[0]
        S = float(min(stop_hit.o.iloc[0], nivel_stop))
        du_s = dias_uteis(para_data(d_saida), venc)
        put_s = preco_opcao(opc_t, "PUT", venc, kp, d_saida, S, iv_p) if du_s > 0 else max(kp - S, 0.0)
        call_s = (preco_opcao(opc_t, "CALL", venc, kc, d_saida, S, iv_c) if du_s > 0 else max(S - kc, 0.0)) if kc else 0.0
        status = "stop"
        var_max = (janela.h[janela.index <= d_saida].max() / C - 1) * 100
    elif not toque.empty:
        d_saida = toque.index[0]
        S = float(max(toque.o.iloc[0], alvo))
        put_s = preco_opcao(opc_t, "PUT", venc, kp, d_saida, S, iv_p)
        call_s = preco_opcao(opc_t, "CALL", venc, kc, d_saida, S, iv_c) if kc else 0.0
        status = "alvo"
        var_max = (toque.h.iloc[0] / C - 1) * 100
    elif corte_feito:
        d_saida = janela.index[-1]
        S = float(janela.c.iloc[-1])
        du_s = dias_uteis(para_data(d_saida), venc)
        put_s = preco_opcao(opc_t, "PUT", venc, kp, d_saida, S, iv_p) if du_s > 0 else max(kp - S, 0.0)
        call_s = (preco_opcao(opc_t, "CALL", venc, kc, d_saida, S, iv_c) if du_s > 0 else max(S - kc, 0.0)) if kc else 0.0
        status = "corte"
        var_max = (janela.h.max() / C - 1) * 100
    elif vencida:
        d_saida = px_t.index[px_t.index <= venc_s][-1]
        S = float(px_t.c[d_saida])
        put_s, call_s = max(kp - S, 0.0), (max(S - kc, 0.0) if kc else 0.0)
        status = "vencimento"
        var_max = (px_t.h[(px_t.index > op.dataoperacao) & (px_t.index <= venc_s)].max() / C - 1) * 100
    else:
        out["status"] = "aberta"
        fim = venc_s
        if CORTE_DIAS:
            fim = min(fim, ymd(pd.Timestamp(np.busday_offset(np.datetime64(d0), CORTE_DIAS, roll="forward")).date()))
        out["fim_capital"] = fim
        return out
    valor = S + put_s - call_s
    out.update(status=status, SAIDA_data=d_saida, var_MAX_ativo=round(var_max, 2),
               retorno=round((valor - custo) / custo * 100, 2),
               saida_put=round(put_s, 2), saida_ativo=round(S, 2), saida_call=round(-call_s, 2) if kc else None,
               dias_capital=max(int(np.busday_count(d0, para_data(d_saida))), 1), fim_capital=d_saida)
    return out


def marcar_capital_unico(df):
    """Capital todo numa operacao de cada vez: entra a 1.a operacao (por data e ordem) com o capital livre."""
    df["selecionada_capital_unico"] = False
    ocupado_ate = ""
    validas = df[df.status.isin(["alvo", "corte", "vencimento", "stop", "aberta"])]
    for i in validas.sort_values(["dataoperacao", "ordem_selecao_dia", "ticker"]).index:
        if df.at[i, "dataoperacao"] < ocupado_ate:
            continue
        df.at[i, "selecionada_capital_unico"] = True
        ocupado_ate = df.at[i, "fim_capital"]
    return df


COLUNAS_CALL = ["strike_call", "valor_call", "bid_call", "ask_call", "vol_call", "entrada_call", "saida_call"]
COLUNAS = [
    "dataoperacao", "hora", "ticker", "estrategia", "close_oper", "ordem_selecao_dia", "rank_mm50_dia", "dist_MM50%",
    "strike_put", "valor_put", "bid_put", "ask_put", "venc_put", "dias_venc", "vol_put",
    "alvo_saida", "stop_saida", "SAIDA_data", "var_MAX_ativo", "varAtualativo", "retorno",
    "entrada_put", "entrada_ativo", "saida_put", "saida_ativo",
    "status", "dias_capital", "selecionada_capital_unico",
]
if CALL_DELTA:  # colunas da call so quando se vende call
    def _inserir_depois(lista, referencia, novas):
        i = lista.index(referencia) + 1
        return lista[:i] + novas + lista[i:]
    COLUNAS = _inserir_depois(COLUNAS, "vol_put", COLUNAS_CALL[:5])
    COLUNAS = _inserir_depois(COLUNAS, "entrada_ativo", ["entrada_call"])
    COLUNAS = _inserir_depois(COLUNAS, "saida_ativo", ["saida_call"])


def construir(con):
    base.ESTRATEGIA, base.ORDEM_MAX = ESTRATEGIA, ORDEM_MAX  # carregar_operacoes le estes do Opcoes_base
    ops = carregar_operacoes(con)
    desde = ops.dataoperacao.min()
    px = carregar_precos(con, ops.ticker.unique(), desde)
    opc = carregar_opcoes(con, ops.ticker.unique(), ymd(para_data(desde) - timedelta(days=DIAS_FALLBACK_CADEIA)))
    df = avaliar_com_travas(con, MODELO, ops, avaliar, px, opc)
    df = marcar_capital_unico(df)
    df = df.reindex(columns=COLUNAS)
    for col in ["hora", "estrategia", "venc_put", "SAIDA_data", "status"]:
        df[col] = df[col].astype("object")
    con.register("df_collar", df)
    con.execute(f"CREATE OR REPLACE TABLE {TABELA_DESTINO} AS SELECT * FROM df_collar ORDER BY dataoperacao DESC, ticker")
    con.unregister("df_collar")
    return df


def construir_volatilidade(con):
    """Volatilidade por mes (e ultimos 20 pregoes) dos tickers com opcoes liquidas e movimento apos os sinais ext.

    Abaixo de VOL_CALMO o mercado esta calmo: estrategias que dependem de movimentos grandes tendem a perder.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {TABELA_VOLATILIDADE} AS
        WITH liq AS (
            SELECT DISTINCT trim(ticker) AS ticker FROM Stocks_OPC_liquidez
            WHERE liquidez_classificacao IN ('Alta', 'Muito alta')
        ),
        px AS (
            SELECT trim(s.ticker) AS ticker, CAST(s.data AS VARCHAR) AS d, TRY_CAST(s.close AS DOUBLE) AS c
            FROM Stocks_Ind s JOIN liq ON liq.ticker = trim(s.ticker)
            WHERE CAST(s.data AS VARCHAR) >= '{DESDE_VOLATILIDADE}'
        ),
        ret AS (
            SELECT ticker, d, c / LAG(c) OVER (PARTITION BY ticker ORDER BY d) - 1 AS r FROM px
        ),
        datas AS (
            SELECT d, ROW_NUMBER() OVER (ORDER BY d DESC) AS n FROM (SELECT DISTINCT d FROM ret)
        ),
        ret_p AS (
            SELECT r.*, substr(r.d, 1, 6) AS mes, CASE WHEN dt.n <= 20 THEN 'ultimos 20 pregoes' END AS recente
            FROM ret r JOIN datas dt USING (d) WHERE r.r IS NOT NULL
        ),
        por_periodo AS (
            SELECT mes AS periodo, * EXCLUDE (mes, recente) FROM ret_p
            UNION ALL
            SELECT recente, * EXCLUDE (mes, recente) FROM ret_p WHERE recente IS NOT NULL
        ),
        vol_ticker AS (
            SELECT periodo, ticker, stddev_samp(r) * sqrt(252) * 100 AS vol
            FROM por_periodo GROUP BY 1, 2 HAVING count(*) >= 5
        ),
        mercado_dia AS (
            SELECT periodo, d, avg(r) AS r FROM por_periodo GROUP BY 1, 2
        ),
        mercado AS (
            SELECT periodo, stddev_samp(r) * sqrt(252) * 100 AS vol_mercado,
                   (exp(sum(ln(1 + r))) - 1) * 100 AS retorno_mercado, count(*) AS pregoes
            FROM mercado_dia GROUP BY 1
        ),
        sinais AS (
            SELECT CAST(op.data_operacao AS VARCHAR) AS d0, trim(op.ticker) AS ticker, max(TRY_CAST(op.close AS DOUBLE)) AS c0
            FROM oper_abertas op JOIN liq ON liq.ticker = trim(op.ticker)
            WHERE TRY_CAST(op.dia_hist AS INTEGER) = 1 AND lower(trim(op.estrategia)) = 'ext'
            GROUP BY 1, 2
        ),
        mov5 AS (
            SELECT d0, ticker, max(sobe) AS sobe, min(cai) AS cai FROM (
                SELECT s.d0, s.ticker, (f.h / s.c0 - 1) * 100 AS sobe, (f.l / s.c0 - 1) * 100 AS cai,
                       ROW_NUMBER() OVER (PARTITION BY s.d0, s.ticker ORDER BY f.d) AS n
                FROM sinais s
                JOIN (
                    SELECT trim(ticker) AS ticker, CAST(data AS VARCHAR) AS d, TRY_CAST(Max AS DOUBLE) AS h, TRY_CAST(Min AS DOUBLE) AS l
                    FROM Stocks_Ind
                ) f ON f.ticker = s.ticker AND f.d > s.d0
            ) WHERE n <= 5 GROUP BY 1, 2 HAVING count(*) = 5
        ),
        sinais_mes AS (
            SELECT substr(d0, 1, 6) AS periodo, count(*) AS sinais_ext,
                   median(sobe) AS subida_5d_mediana, median(cai) AS queda_5d_mediana,
                   100.0 * avg(CASE WHEN sobe >= 4 THEN 1 ELSE 0 END) AS pct_sobe_4,
                   100.0 * avg(CASE WHEN sobe >= 10 OR cai <= -10 THEN 1 ELSE 0 END) AS pct_move_10
            FROM mov5 GROUP BY 1
        )
        SELECT
            m.periodo,
            m.pregoes,
            round(median(v.vol), 1) AS vol_tickers_mediana,
            round(any_value(m.vol_mercado), 1) AS vol_mercado,
            round(any_value(m.retorno_mercado), 1) AS retorno_mercado,
            CASE WHEN median(v.vol) < {VOL_CALMO} THEN 'calmo'
                 WHEN median(v.vol) > {VOL_AGITADO} THEN 'agitado' ELSE 'normal' END AS regime,
            any_value(s.sinais_ext) AS sinais_ext,
            round(any_value(s.subida_5d_mediana), 1) AS subida_5d_mediana,
            round(any_value(s.queda_5d_mediana), 1) AS queda_5d_mediana,
            round(any_value(s.pct_sobe_4), 0) AS pct_sobe_4,
            round(any_value(s.pct_move_10), 0) AS pct_move_10
        FROM mercado m
        JOIN vol_ticker v USING (periodo)
        LEFT JOIN sinais_mes s USING (periodo)
        GROUP BY m.periodo, m.pregoes
        ORDER BY m.periodo
        """
    )


def sincronizar_sqlite(con):
    df = con.execute(f"SELECT * FROM {TABELA_DESTINO}").fetchdf()
    vol = con.execute(f"SELECT * FROM {TABELA_VOLATILIDADE}").fetchdf()
    travas = con.execute(f"SELECT * FROM {TABELA_TRAVAS} ORDER BY dataoperacao DESC, modelo, ticker").fetchdf()
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        df.to_sql(TABELA_DESTINO, sq, if_exists="replace", index=False)
        vol.to_sql(TABELA_VOLATILIDADE, sq, if_exists="replace", index=False)
        travas.to_sql(TABELA_TRAVAS, sq, if_exists="replace", index=False)
    finally:
        sq.close()
    return len(df)


def resumo(df):
    print(df.status.value_counts().to_string())
    fechadas = df[df.status.isin(["alvo", "corte", "vencimento", "stop"])]
    for nome, f in [("todos os sinais", fechadas), ("capital unico", fechadas[fechadas.selecionada_capital_unico])]:
        if not f.empty:
            print(f"{nome:16s} n={len(f):3d} media={f.retorno.mean():+.2f}% acerto={(f.retorno > 0).mean() * 100:.0f}% "
                  f"por_mes={f.retorno.sum() / f.dias_capital.sum() * 21:+.2f}%")


def main():
    con = duckdb.connect(DB_FILE)
    df = construir(con)
    construir_volatilidade(con)
    qtd = sincronizar_sqlite(con)
    print(f"Tabela {TABELA_DESTINO} criada com {qtd} linhas.")
    resumo(df)
    con.close()


if __name__ == "__main__":
    main()
