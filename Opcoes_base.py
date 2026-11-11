"""Funcoes comuns dos modelos de opcoes (Evolucao_stock, Evolucao_call_put, Evolucao_vol_call_put, Vol_momento, Completar_opcoes_*).

Sinais   carregar_operacoes: sinais de oper_abertas (dia_hist = 1) com opcoes de liquidez Alta / Muito alta, filtrados por
         ESTRATEGIA e ORDEM_MAX (cada modelo define os seus antes de chamar: base.ESTRATEGIA, base.ORDEM_MAX = ...).
Precos   carregar_precos (Stocks_Ind) e carregar_opcoes (Call_Put_dados); cadeia = opcoes do registo do dia (ou do mais
         recente ate DIAS_FALLBACK_CADEIA dias antes); preco_opcao = Black-Scholes (bs_opcao do Evolucao_opc_collar) com a
         IV da recolha, sem IV usa o lastPrice; escolher_por_delta = contrato com delta mais proximo (ate DELTA_TOLERANCIA).
Prazo    dias_uteis do dia seguinte ate ao fim (inclusive); venc_valido = vencimento aceite para as pernas (desde 2026-10-27:
         sexta-feira com >= 10 dias uteis; antes >= 8).
Trava    sinal de hoje: na 1.a rodada em que tem entrada, vencimento, strike, IV e valor das opcoes vao para
         TABELA_TRAVAS; as rodadas seguintes do dia (e dos dias seguintes) usam essa entrada, nao a cadeia
         recolhida depois. So vale enquanto a hora do sinal em oper_abertas for a mesma (reprocessado: escolhe de novo).
Antes    estas funcoes estavam no Evolucao_put_30.py (separado em Opcoes_base.py e Evolucao_stock.py em 2026-10-24).
"""
import math
from datetime import date, datetime, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd

from Evolucao_opc_collar import bs_opcao


DB_FILE = r"C:\BD\Stocks.duckdb"
SQLITE_FILE = r"C:\Metabase\data\visualizacao.sqlite"
TABELA_TRAVAS = "Entrada_opc_travada"  # entrada das opcoes (strike, IV, valor) dos sinais do dia, gravada na 1.a rodada

ESTRATEGIA = None    # None = ext + fase2; "ext" ou "fase2" para so uma estrategia (definido pelo modelo que chama)
ORDEM_MAX = 1        # so sinais com ordem_selecao_dia <= ORDEM_MAX (None = todos)
DIAS_UTEIS_MIN_VENC = 10        # sinais desde DATA_REGRA_VENC: vencimento a sexta-feira com >= 10 dias uteis
DIAS_UTEIS_MIN_VENC_ANTES = 8   # sinais anteriores: >= 8 dias uteis (regra ate 2026-10-26, mantem o historico)
DATA_REGRA_VENC = "20261027"
DELTA_TOLERANCIA = 0.10  # sem opcao com delta a menos disto do pretendido: sem_put / sem_call
DIAS_FALLBACK_CADEIA = 3
JUROS = 0.04

_N = NormalDist().cdf


def para_data(valor) -> date:
    if isinstance(valor, date):
        return valor
    return datetime.strptime(str(valor).replace("-", "")[:8], "%Y%m%d").date()


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def dias_uteis(inicio: date, fim: date) -> int:
    # dias uteis do dia seguinte a inicio ate fim (inclusive)
    return int(np.busday_count(inicio + timedelta(days=1), fim + timedelta(days=1)))


def venc_valido(dia, venc) -> bool:
    """Vencimento aceite para as pernas de um sinal do dia: desde DATA_REGRA_VENC sexta-feira com >= DIAS_UTEIS_MIN_VENC
    dias uteis; antes >= DIAS_UTEIS_MIN_VENC_ANTES dias uteis (qualquer dia da semana)."""
    d0 = para_data(dia)
    if ymd(d0) >= DATA_REGRA_VENC:
        return venc.weekday() == 4 and dias_uteis(d0, venc) >= DIAS_UTEIS_MIN_VENC
    return dias_uteis(d0, venc) >= DIAS_UTEIS_MIN_VENC_ANTES


def delta_bs(spot, strike, du, vol, tipo):
    if vol is None or vol != vol or vol <= 0 or du <= 0:
        return None
    if vol > 5:
        vol = vol / 100
    t = du / 252
    d1 = (math.log(spot / strike) + (vol * vol / 2 + math.log(1 + JUROS)) * t) / (vol * math.sqrt(t))
    return _N(d1) if tipo == "CALL" else _N(d1) - 1


def filtro_liquidez_desde(con, alias="l", col_data="op.data_operacao"):
    """Ticker promovido pelo Liquidez_opc: so os sinais a partir do dia da promocao (liquidez_desde)."""
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'Stocks_OPC_liquidez'").fetchall()}
    if "liquidez_desde" not in cols:
        return ""
    return f"AND ({alias}.liquidez_desde IS NULL OR CAST({col_data} AS VARCHAR) >= {alias}.liquidez_desde)"


def carregar_operacoes(con):
    filtro_estrategia = f"AND lower(trim(op.estrategia)) = '{ESTRATEGIA}'" if ESTRATEGIA else ""
    filtro_desde = filtro_liquidez_desde(con)
    filtro_ordem = f"HAVING MIN(TRY_CAST(op.ordem_selecao_dia AS INTEGER)) <= {int(ORDEM_MAX)}" if ORDEM_MAX else ""
    return con.execute(
        f"""
        SELECT
            CAST(op.data_operacao AS VARCHAR) AS dataoperacao,
            trim(op.ticker) AS ticker,
            MIN(op.hora) AS hora,
            string_agg(DISTINCT lower(trim(op.estrategia)), '+' ORDER BY lower(trim(op.estrategia))) AS estrategia,
            MIN(TRY_CAST(op.ordem_selecao_dia AS INTEGER)) AS ordem_selecao_dia,
            MIN(TRY_CAST(op.rank_mm50_dia AS INTEGER)) AS rank_mm50_dia,
            MAX(TRY_CAST(op."dist_MM50%" AS DOUBLE)) AS dist_mm50,
            MAX(TRY_CAST(op.close AS DOUBLE)) AS close_oper
        FROM oper_abertas AS op
        INNER JOIN Stocks_OPC_liquidez AS l
            ON trim(l.ticker) = trim(op.ticker)
        WHERE TRY_CAST(op.dia_hist AS INTEGER) = 1
          AND l.liquidez_classificacao IN ('Alta', 'Muito alta')
          {filtro_desde}
          {filtro_estrategia}
        GROUP BY 1, 2
        {filtro_ordem}
        ORDER BY 1, 3, 2
        """
    ).fetchdf()


def carregar_precos(con, tickers, desde):
    lista = ",".join("'" + t.replace("'", "''") + "'" for t in tickers)
    df = con.execute(
        f"""
        SELECT trim(ticker) AS ticker, CAST(data AS VARCHAR) AS d,
               TRY_CAST(Open AS DOUBLE) AS o, TRY_CAST(Max AS DOUBLE) AS h, TRY_CAST(Min AS DOUBLE) AS l,
               TRY_CAST(close AS DOUBLE) AS c
        FROM Stocks_Ind
        WHERE trim(ticker) IN ({lista}) AND CAST(data AS VARCHAR) >= '{desde}'
        ORDER BY 1, 2
        """
    ).fetchdf()
    return {t: g.set_index("d") for t, g in df.groupby("ticker")}


# Indicadores do sinal (colunas das etapas 1 a 4; antes so no Evolucao_etapa3.py, movidos em 2026-11-11)
INDICADORES = ["liquidez", "MOM20", "dist_MM50%", "VR10", "vol_hist20", "MIN %", "MAX %", "maior_mov %"]
JANELA_VOL = 20  # vol_hist20: vol realizada anualizada (%) dos 20 retornos diarios ate ao dia do sinal
DIAS_MOV = 7     # MIN % / MAX %: menor Min e maior Max do D1 ao D7 face ao close do sinal


def carregar_indicadores(con, tickers, desde):
    inicio = (pd.Timestamp(desde) - pd.Timedelta(days=60)).strftime("%Y%m%d")
    px = con.execute(
        """
        SELECT CAST(data AS VARCHAR) AS d, trim(ticker) AS ticker, TRY_CAST(close AS DOUBLE) AS c,
               TRY_CAST(Max AS DOUBLE) AS h, TRY_CAST(Min AS DOUBLE) AS l,
               TRY_CAST(MOM20 AS DOUBLE) AS mom20, TRY_CAST("dist_MM50%" AS DOUBLE) AS dmm50, TRY_CAST(VR10 AS DOUBLE) AS vr10
        FROM Stocks_Ind
        WHERE CAST(data AS VARCHAR) >= ? AND trim(ticker) IN (SELECT unnest(?))
        """, [inicio, list(tickers)]).fetchdf()
    return {t: g.drop_duplicates("d", keep="last").set_index("d").sort_index() for t, g in px.groupby("ticker")}


def indicadores(op, p):
    """MOM20, dist_MM50% e VR10 da Stocks_Ind no dia do sinal; vol_hist20; MIN % / MAX % do D1 ao D7; maior_mov % = maior |MIN|, |MAX|."""
    vazio = dict.fromkeys(INDICADORES[1:], np.nan)
    if p is None or op.dataoperacao not in p.index or not op.close_oper == op.close_oper:
        return vazio
    s = p.loc[op.dataoperacao]
    hist = p.c[p.index <= op.dataoperacao].tail(JANELA_VOL + 1)
    vol = float(np.log(hist).diff().std() * np.sqrt(252) * 100) if len(hist) > JANELA_VOL else np.nan
    fut = p[p.index > op.dataoperacao].iloc[:DIAS_MOV]
    c0 = float(op.close_oper)
    mn = (fut.l.min() / c0 - 1) * 100 if len(fut) else np.nan
    mx = (fut.h.max() / c0 - 1) * 100 if len(fut) else np.nan
    return {"MOM20": s.mom20, "dist_MM50%": s.dmm50, "VR10": s.vr10, "vol_hist20": vol, "MIN %": mn, "MAX %": mx,
            "maior_mov %": max(abs(mn), abs(mx)) if len(fut) else np.nan}


def calcular_indicadores(con, ops):
    """INDICADORES (sem arredondar) para cada linha de ops (dataoperacao, ticker, close_oper), no mesmo indice;
    liquidez = classificacao atual da Stocks_OPC_liquidez."""
    if ops.empty:
        return pd.DataFrame(columns=INDICADORES, index=ops.index)
    ind_px = carregar_indicadores(con, ops.ticker.unique(), ops.dataoperacao.min())
    ind = pd.DataFrame([indicadores(op, ind_px.get(op.ticker)) for op in ops.itertuples()], index=ops.index)
    liq = con.execute("SELECT trim(ticker) AS t, any_value(liquidez_classificacao) AS l FROM Stocks_OPC_liquidez GROUP BY 1").fetchdf()
    ind.insert(0, "liquidez", ops.ticker.map(liq.set_index("t").l))
    return ind


def juntar_indicadores(con, df):
    """Acrescenta a df as colunas INDICADORES (arredondadas a 2 casas)."""
    ind = calcular_indicadores(con, df)
    for col in INDICADORES:
        df[col] = ind[col].values if col == "liquidez" else pd.to_numeric(ind[col], errors="coerce").round(2).values
    return df


def carregar_opcoes(con, tickers, desde):
    lista = ",".join("'" + t.upper().replace("'", "''") + "'" for t in tickers)
    df = con.execute(
        f"""
        SELECT upper(trim(ticker)) AS ticker, upper(trim(tipo)) AS tipo, CAST(DataRegistro AS VARCHAR) AS dr,
               CAST(dataStrike AS DATE) AS venc, TRY_CAST(strike AS DOUBLE) AS strike,
               MAX(TRY_CAST(lastPrice AS DOUBLE)) AS lp, MAX(TRY_CAST(bid AS DOUBLE)) AS bid,
               MAX(TRY_CAST(ask AS DOUBLE)) AS ask, MAX(TRY_CAST(impliedVolatility AS DOUBLE)) AS iv
        FROM Call_Put_dados
        WHERE upper(trim(ticker)) IN ({lista}) AND CAST(DataRegistro AS VARCHAR) >= '{desde}'
        GROUP BY ALL
        """
    ).fetchdf()
    df["venc"] = pd.to_datetime(df.venc).dt.date
    return {t: g for t, g in df.groupby("ticker")}


def cadeia(opc_t, tipo, dia, venc=None):
    """Opcoes do registo do dia (ou do mais recente ate DIAS_FALLBACK_CADEIA dias antes)."""
    lim = ymd(para_data(dia) - timedelta(days=DIAS_FALLBACK_CADEIA))
    c = opc_t[(opc_t.tipo == tipo) & (opc_t.dr <= dia) & (opc_t.dr >= lim) & (opc_t.lp > 0)]
    if venc is not None:
        c = c[c.venc == venc]
    if c.empty:
        return c
    return c[c.dr == c.dr.max()]


def escolher_por_delta(c, spot, du, alvo_delta, tipo):
    if c.empty:
        return None
    c = c.assign(delta=[delta_bs(spot, k, du, v, tipo) for k, v in zip(c.strike, c.iv)]).dropna(subset=["delta"])
    if c.empty:
        return None
    melhor = c.iloc[(c.delta - alvo_delta).abs().argsort().iloc[0]]
    return melhor if abs(melhor.delta - alvo_delta) <= DELTA_TOLERANCIA else None


def escolher_put_por_distancia(c, spot, du, distancia):
    """Put com o maior strike <= spot * (1 - distancia)."""
    c = c[c.strike <= spot * (1 - distancia)]
    if c.empty:
        return None
    melhor = c.sort_values("strike").iloc[-1].copy()
    melhor["delta"] = delta_bs(spot, melhor.strike, du, melhor.iv, "PUT")
    return melhor


def preco_opcao(opc_t, tipo, venc, strike, dia, spot, iv_fallback):
    c = cadeia(opc_t, tipo, dia, venc)
    linha = c[c.strike == strike] if not c.empty else c
    iv = linha.iv.iloc[0] if not linha.empty else None
    if iv is None or iv != iv or iv <= 0:
        iv = iv_fallback
    v = bs_opcao(spot, strike, dia, venc, iv, tipo)
    if v is None and not linha.empty:
        v = float(linha.lp.iloc[0])
    return v


def carregar_travas(con, modelo):
    """Entradas travadas do modelo: {(dataoperacao, ticker, hora): linha}. A hora tem de bater com a do sinal em oper_abertas."""
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABELA_TRAVAS} (
            modelo VARCHAR, dataoperacao VARCHAR, ticker VARCHAR, hora VARCHAR, close_oper DOUBLE,
            venc_call DATE, strike_call DOUBLE, iv_call DOUBLE, valor_call DOUBLE, bid_call DOUBLE, ask_call DOUBLE,
            venc_put DATE, strike_put DOUBLE, iv_put DOUBLE, valor_put DOUBLE, bid_put DOUBLE, ask_put DOUBLE,
            gravado_em VARCHAR
        )
        """
    )
    df = con.execute(f"SELECT * FROM {TABELA_TRAVAS} WHERE modelo = ?", [modelo]).fetchdf()
    for col in ["venc_call", "venc_put"]:
        df[col] = pd.to_datetime(df[col]).dt.date
    df = df.astype(object).where(df.notna(), None)
    return {(r["dataoperacao"], r["ticker"], r["hora"]): r for r in df.to_dict("records")}


def gravar_travas(con, modelo, linhas):
    """Grava (ou substitui) as entradas dos sinais de hoje que ainda nao estavam travadas."""
    if not linhas:
        return 0
    df = pd.DataFrame(linhas).assign(modelo=modelo, gravado_em=datetime.now().strftime("%Y-%m-%d %H:%M"))
    cols = [r[1] for r in con.execute(f"PRAGMA table_info('{TABELA_TRAVAS}')").fetchall()]
    con.register("df_travas", df.reindex(columns=cols))
    con.execute(
        f"""
        DELETE FROM {TABELA_TRAVAS}
        WHERE (modelo, dataoperacao, ticker) IN (SELECT (modelo, dataoperacao, ticker) FROM df_travas)
        """
    )
    con.execute(f"INSERT INTO {TABELA_TRAVAS} BY NAME SELECT * FROM df_travas")
    con.unregister("df_travas")
    return len(df)


def avaliar_com_travas(con, modelo, ops, avaliar_fn, px, opc):
    """Avalia cada sinal; os sinais de hoje gravam a entrada na 1.a rodada e as rodadas seguintes reutilizam-na."""
    travas = carregar_travas(con, modelo)
    hoje = ymd(date.today())
    linhas, novas = [], []
    for op in ops.itertuples():
        trava = travas.get((op.dataoperacao, op.ticker, op.hora))
        r = avaliar_fn(op, px.get(op.ticker), opc.get(op.ticker.upper()), trava)
        e = r.pop("_entrada", None)
        if e is not None and trava is None and op.dataoperacao == hoje and op.hora:
            novas.append(dict(e, dataoperacao=op.dataoperacao, ticker=op.ticker, hora=op.hora, close_oper=op.close_oper))
        linhas.append(r)
    gravadas = gravar_travas(con, modelo, novas)
    if gravadas:
        print(f"{modelo}: {gravadas} entradas travadas ({', '.join(n['ticker'] for n in novas)})")
    return pd.DataFrame(linhas)
