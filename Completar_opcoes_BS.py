"""Completa Call_Put_dados com opcoes calculadas por Black-Scholes para sinais sem cadeia de opcoes.

Para cada sinal dos meses (MESES, ou os passados na linha de comando) com status sem_opcoes / sem_vencimento / sem_put em TABELA_SINAIS,
grava no dia do sinal (DataRegistro = dataoperacao) uma cadeia sintetica de PUT e CALL:
  - vencimentos: as VENCIMENTOS primeiras sextas-feiras com >= DIAS_UTEIS_MIN_VENC dias uteis;
  - strikes: de -FAIXA a +FAIXA em torno do close, no passo usual do preco;
  - volatilidade: IV ATM da 1.a cadeia real do ticker (>= 20 linhas); se essa cadeia estiver a mais de
    DIAS_REF_MAX dias do sinal, media entre essa IV e a vol realizada de 20 dias ajustada pela razao IV/RV
    do dia de referencia;
  - preco: bs_opcao do Evolucao_opc_collar (mesma formula do Opcoes_base); bid = ask = lastPrice.
As linhas ficam marcadas com ID terminado em _BS, volume = 0 e openInterest = 0.
Remover: DELETE FROM Call_Put_dados WHERE ID LIKE '%\\_BS' ESCAPE '\\'

Uso: python Completar_opcoes_BS.py [AAAAMM ...] [--tabela=Evolucao_etapa3] [--simular]
     --tabela: tabela de sinais (com dataoperacao, ticker, close_oper, status) no SQLite; omissao TABELA_SINAIS.
     2026-11-11: corrido com --tabela=Evolucao_etapa3 de 202510 a 202608 (ordens 2 e 3 sem cadeia; muda tambem a etapa2).
"""
import sqlite3
import sys
from datetime import timedelta

import duckdb
import numpy as np
import pandas as pd

from Opcoes_base import delta_bs, dias_uteis, para_data, venc_valido, ymd
from Evolucao_opc_collar import bs_opcao


DB_FILE = r"C:\BD\Stocks.duckdb"
SQLITE_FILE = r"C:\Metabase\data\visualizacao.sqlite"
TABELA_SINAIS = "Evolucao_stock"
MESES = ["202608"]
STATUS_SEM_DADOS = ("sem_opcoes", "sem_vencimento", "sem_put")
VENCIMENTOS = 2
FAIXA = 0.25
DIAS_REF_MAX = 7
JANELA_RV = 20
IV_MINIMA = 0.10  # cadeia de referencia com IV ATM abaixo disto e ignorada (IV mal gravada)


def passo_strike(preco):
    if preco < 25:
        return 0.5
    if preco < 100:
        return 1.0
    if preco < 250:
        return 2.5
    return 5.0


def sextas(d0, n):
    out, d = [], d0 + timedelta(days=1)
    while len(out) < n:
        if d.weekday() == 4 and venc_valido(d0, d):
            out.append(d)
        d += timedelta(days=1)
    return out


def vol_realizada(px_t, dia):
    g = px_t[px_t.d <= dia].tail(JANELA_RV + 1)
    return float(np.log(g.c).diff().std() * np.sqrt(252))


def vol_sinal(con, px_t, ticker, dia):
    ref = con.execute(
        f"""
        WITH r AS (
            -- cadeia real mais proxima com IV ATM valida (algumas recolhas gravam a IV como ~0)
            SELECT CAST(DataRegistro AS VARCHAR) AS dr
            FROM Call_Put_dados
            WHERE upper(trim(ticker)) = '{ticker}' AND ID NOT LIKE '%\\_BS' ESCAPE '\\'
            GROUP BY 1
            HAVING count(*) >= 20
               AND median(CASE WHEN abs(strike / precoAtual - 1) <= 0.06 THEN impliedVolatility END) >= {IV_MINIMA}
            ORDER BY abs(date_diff('day', strptime(dr, '%Y%m%d'), DATE '{para_data(dia)}')) LIMIT 1
        )
        SELECT r.dr, c.precoAtual, c.dataStrike, c.strike, c.impliedVolatility AS iv
        FROM Call_Put_dados c JOIN r ON CAST(c.DataRegistro AS VARCHAR) = r.dr
        WHERE upper(trim(c.ticker)) = '{ticker}'
        """
    ).fetchdf()
    if ref.empty:
        return vol_realizada(px_t, dia), "rv20 (sem cadeia real)"
    dr, spot = ref.dr.iloc[0], ref.precoAtual.iloc[0]
    vencs = sorted(ref.dataStrike.unique())[:2]
    atm = ref[ref.dataStrike.isin(vencs) & ((ref.strike / spot - 1).abs() <= 0.06) & ref.iv.between(0.05, 5)]
    iv_ref = float(atm.iv.median())
    if abs((para_data(dr) - para_data(dia)).days) <= DIAS_REF_MAX:
        return iv_ref, f"iv atm {dr}"
    escalada = vol_realizada(px_t, dia) * iv_ref / vol_realizada(px_t, dr)
    return (iv_ref + escalada) / 2, f"media iv atm {dr} ({iv_ref:.2f}) e rv20 escalada ({escalada:.2f})"


def cadeia_bs(ticker, dia, spot, vol, vencimentos=None, strikes_extra=()):
    """vencimentos: lista de datas (None = as VENCIMENTOS primeiras sextas com >= DIAS_UTEIS_MIN_VENC dias uteis).
    strikes_extra: strikes a incluir sempre (ex.: os de uma operacao ja aberta)."""
    d0 = para_data(dia)
    passo = passo_strike(spot)
    ks = np.arange(np.floor(spot * (1 - FAIXA) / passo) * passo, spot * (1 + FAIXA) + passo, passo)
    ks = sorted({round(float(k), 2) for k in ks} | {round(float(k), 2) for k in strikes_extra if k == k})
    linhas = []
    for venc in vencimentos or sextas(d0, VENCIMENTOS):
        for tipo in ("PUT", "CALL"):
            for k in ks:
                k = round(float(k), 2)
                v = bs_opcao(spot, k, dia, venc, vol, tipo)
                if v is None:
                    continue
                v = max(v, 0.01)  # preco minimo negociado
                linhas.append(dict(
                    ID=f"{ticker}_{venc.isoformat()}_{tipo}_{k:g}_{dia}_BS", ticker=ticker, precoAtual=spot, tipo=tipo,
                    dataStrike=venc.isoformat(), strike=k, lastPrice=round(v, 2), bid=round(v, 2), ask=round(v, 2),
                    volume=0, openInterest=0, impliedVolatility=round(vol, 4),
                    inTheMoney=(k < spot) if tipo == "CALL" else (k > spot), DataRegistro=dia,
                    delta=round(delta_bs(spot, k, dias_uteis(d0, venc), vol, tipo), 3),
                ))
    return pd.DataFrame(linhas)


def main():
    simular = "--simular" in sys.argv
    meses = [a for a in sys.argv[1:] if a.isdigit() and len(a) == 6] or MESES
    tabela = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--tabela=")), TABELA_SINAIS)
    filtro_mes = " OR ".join(f"dataoperacao LIKE '{m}%'" for m in meses)
    sq = sqlite3.connect(SQLITE_FILE)
    status = ",".join(f"'{s}'" for s in STATUS_SEM_DADOS)
    sinais = pd.read_sql(
        f"SELECT dataoperacao, ticker, close_oper, status FROM {tabela} "
        f"WHERE ({filtro_mes}) AND status IN ({status}) ORDER BY dataoperacao", sq)
    sq.close()
    if sinais.empty:
        print("Nenhum sinal sem opcoes.")
        return
    con = duckdb.connect(DB_FILE, read_only=simular, config={"memory_limit": "2GB"})
    lista = ",".join(f"'{t}'" for t in sinais.ticker.unique())
    px = con.execute(
        f"SELECT trim(ticker) AS t, CAST(data AS VARCHAR) AS d, TRY_CAST(close AS DOUBLE) AS c FROM Stocks_Ind "
        f"WHERE trim(ticker) IN ({lista}) ORDER BY 1, 2").fetchdf()
    novas = []
    for s in sinais.itertuples():
        t = s.ticker.upper()
        vol, origem = vol_sinal(con, px[px.t == s.ticker], t, s.dataoperacao)
        df = cadeia_bs(t, s.dataoperacao, float(s.close_oper), vol)
        existentes = con.execute(
            f"SELECT upper(trim(tipo)) AS tipo, CAST(dataStrike AS VARCHAR) AS dataStrike, TRY_CAST(strike AS DOUBLE) AS strike "
            f"FROM Call_Put_dados WHERE upper(trim(ticker)) = '{t}' AND CAST(DataRegistro AS VARCHAR) = '{s.dataoperacao}'").fetchdf()
        if not existentes.empty:
            df = df.merge(existentes.assign(_x=1), how="left", on=["tipo", "dataStrike", "strike"])
            df = df[df._x.isna()].drop(columns="_x")
        put40 = df[(df.tipo == "PUT") & (df.dataStrike == df.dataStrike.min())]
        put40 = put40.iloc[(put40.delta + 0.40).abs().argsort().iloc[0]]
        print(f"{s.dataoperacao} {t:5s} close {s.close_oper:8.2f} vol {vol:.3f} [{origem}] -> {len(df)} linhas, "
              f"put -0.40: venc {put40.dataStrike} strike {put40.strike:g} delta {put40.delta} preco {put40.lastPrice}")
        novas.append(df)
    novas = pd.concat(novas).drop(columns="delta")
    if simular:
        print(f"Simulacao: {len(novas)} linhas nao gravadas.")
    else:
        con.register("df_bs", novas)
        con.execute("INSERT INTO Call_Put_dados BY NAME SELECT * FROM df_bs")
        con.unregister("df_bs")
        print(f"Gravadas {len(novas)} linhas em Call_Put_dados (ID terminado em _BS).")
    con.close()


if __name__ == "__main__":
    main()
