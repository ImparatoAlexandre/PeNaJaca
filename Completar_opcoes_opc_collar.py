"""Completa Call_Put_dados com opcoes Black-Scholes para as operacoes do Evolucao_opc_collar desde DESDE.

Repete ate nao faltar nada (no maximo PASSES vezes):
  1. corre o Evolucao_opc_collar (reconstroi a tabela);
  2. procura operacoes sem dados de opcao em cada etapa:
       - sem put           -> cadeia no dia da operacao (vencimentos: sextas com >= 8 dias uteis), spot = close_oper
       - sem call etapa 2  -> cadeia no dia da etapa 2, no vencimento da put, spot = close desse dia
       - etapa 3 sem preco -> cadeia no dia da etapa 3, no vencimento da put, spot = close desse dia
  3. grava as cadeias em falta (mesma calculadora e volatilidade do Completar_opcoes_BS; ID terminado em _BS).
Linhas que ja existem (mesmo ticker, dia, tipo, vencimento e strike) nao sao duplicadas.

Uso: python Completar_opcoes_opc_collar.py [AAAAMMDD] [--simular]
"""
import contextlib
import io
import sys

import duckdb
import pandas as pd

import Evolucao_opc_collar as opc_collar
from Completar_opcoes_BS import DB_FILE, cadeia_bs, vol_sinal
from Opcoes_base import para_data


DESDE = "20260817"
PASSES = 4


def faltas(con, desde):
    t = opc_collar.TABELA_DESTINO
    return con.execute(
        f"""
        SELECT 'put' AS etapa, dataoperacao AS dia, upper(trim(ticker)) AS ticker, close_oper AS spot, NULL AS venc,
               dataoperacao, ticker AS ticker_op, NULL AS strike_put, NULL AS strike_call
        FROM {t} WHERE dataoperacao >= '{desde}' AND strike_put IS NULL
        UNION ALL
        SELECT 'call_e2', d."ETAPA_2_data", upper(trim(d.ticker)), NULL, CAST(d.venc_put AS DATE), d.dataoperacao, d.ticker,
               d.strike_put, NULL
        FROM {t} d WHERE d.dataoperacao >= '{desde}' AND d.strike_put IS NOT NULL AND d."ETAPA_2_data" IS NOT NULL
             AND d.strike_call IS NULL
        UNION ALL
        SELECT 'precos_e3', d."ETAPA3_data", upper(trim(d.ticker)), NULL, CAST(d.venc_put AS DATE), d.dataoperacao, d.ticker,
               d.strike_put, d.strike_call
        FROM {t} d WHERE d.dataoperacao >= '{desde}' AND d.strike_call IS NOT NULL AND d."ETAPA3_data" IS NOT NULL
             AND d.retorno IS NULL
        """
    ).fetchdf()


def gerar(con, f):
    px = con.execute(
        "SELECT upper(trim(ticker)) AS t, CAST(data AS VARCHAR) AS d, TRY_CAST(close AS DOUBLE) AS c FROM Stocks_Ind "
        f"WHERE upper(trim(ticker)) IN ({','.join(repr(x) for x in f.ticker.unique())}) ORDER BY 1, 2").fetchdf()
    novas = []
    for r in f.drop_duplicates(["etapa", "dia", "ticker", "venc", "strike_put", "strike_call"]).itertuples():
        px_t = px[px.t == r.ticker]
        spot = r.spot if r.spot == r.spot and r.spot is not None else float(px_t[px_t.d == r.dia].c.iloc[0])
        vol, origem = vol_sinal(con, px_t, r.ticker, r.dia)
        vencs = None if r.venc is None or r.venc != r.venc else [pd.Timestamp(r.venc).date()]
        df = cadeia_bs(r.ticker, r.dia, float(spot), vol, vencs, [r.strike_put, r.strike_call])
        existentes = con.execute(
            "SELECT upper(trim(tipo)) AS tipo, CAST(dataStrike AS VARCHAR) AS dataStrike, TRY_CAST(strike AS DOUBLE) AS strike "
            f"FROM Call_Put_dados WHERE upper(trim(ticker)) = '{r.ticker}' AND CAST(DataRegistro AS VARCHAR) = '{r.dia}'").fetchdf()
        if not existentes.empty:
            df = df.merge(existentes.assign(_x=1), how="left", on=["tipo", "dataStrike", "strike"])
            df = df[df._x.isna()].drop(columns="_x")
        print(f"  {r.etapa:9s} {r.dataoperacao} {r.ticker:5s} dia {r.dia} spot {float(spot):8.2f} vol {vol:.3f} "
              f"venc {vencs[0] if vencs else 'sextas'} -> {len(df)} linhas [{origem}]")
        novas.append(df)
    return pd.concat(novas).drop(columns="delta") if novas else pd.DataFrame()


def correr_opc_collar():
    with contextlib.redirect_stdout(io.StringIO()):
        opc_collar.main()


def main():
    desde = next((a for a in sys.argv[1:] if a.isdigit() and len(a) == 8), DESDE)
    simular = "--simular" in sys.argv
    for passe in range(1, PASSES + 1):
        correr_opc_collar()
        con = duckdb.connect(DB_FILE, read_only=simular)
        f = faltas(con, desde)
        print(f"Passe {passe}: {len(f)} falta(s) " + str(f.etapa.value_counts().to_dict()))
        novas = gerar(con, f) if not f.empty else pd.DataFrame()
        if novas.empty or simular:
            con.close()
            break
        con.register("df_bs", novas)
        con.execute("INSERT INTO Call_Put_dados BY NAME SELECT * FROM df_bs")
        con.unregister("df_bs")
        con.close()
        print(f"  gravadas {len(novas)} linhas")
    if not simular:
        correr_opc_collar()


if __name__ == "__main__":
    main()
