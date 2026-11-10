"""Inclui tickers novos (ja acrescentados a lista `stocks` do Atualizar_Stocks_Ind.py) na Stocks_Ind e na Stocks_OPC_liquidez.

1. Stocks_Ind: historico desde a 1.a data da tabela, calculado com as funcoes do Atualizar_Stocks_Ind.py (mesmos indicadores).
2. Stocks_OPC_liquidez: le a liquidez das opcoes hoje (Liquidez_opc.ler_ticker) e grava a linha do ticker com
   liquidez_base / liquidez_classificacao = Alta (ou Media para os de --media) e liquidez_desde = hoje: os modelos
   so usam os sinais do ticker a partir de hoje (o historico dos modelos nao muda). Tambem grava a recolha de hoje em
   Stocks_OPC_liquidez_hist e sincroniza o SQLite.
Correr depois do Liquidez_opc.py (cria as colunas liquidez_base / liquidez_desde e a tabela _hist).
Uso   python Incluir_tickers.py BABA ONON SE NVO STNE XPEV --media ASX
"""
import re
import sys
from datetime import datetime

import duckdb
import pandas as pd
import yfinance as yf

import Liquidez_opc as L


def carregar_historico(con, tickers):
    src = open("Atualizar_Stocks_Ind.py", encoding="utf-8-sig").read()
    g = {"__name__": "incluir_tickers"}
    exec(src[src.index("# IMPORTS"):src.index("# BUSCA E CALCULO")], g)   # so as funcoes, sem correr a lista toda
    g["CALC_LOOKBACK"], g["Periodo"] = 1000, 1000
    desde = con.execute("SELECT min(CAST(data AS VARCHAR)) FROM Stocks_Ind").fetchone()[0]
    lote = yf.download(tickers=tickers, period="2y", auto_adjust=False, group_by="ticker", threads=True, progress=False)
    partes = []
    for t in tickers:
        df = g["processar_ticker"](t, lote, None)
        if "Stock_ID" not in df:
            print(f"{t}: sem dados no Yahoo")
            continue
        df = df[df.data.astype(str) >= desde]
        partes.append(df)
        print(f"{t}: {len(df)} pregoes ({df.data.min()} a {df.data.max()})")
    if not partes:
        return
    db_cols = eval(re.search(r"db_cols = (\[.*?\])", src).group(1))
    final = pd.concat(partes, ignore_index=True)[db_cols]
    con.register("novos_ind", final)
    con.execute("DELETE FROM Stocks_Ind WHERE Stock_ID IN (SELECT Stock_ID FROM novos_ind)")
    con.execute("INSERT INTO Stocks_Ind BY NAME SELECT * FROM novos_ind")
    con.unregister("novos_ind")


def gravar_liquidez(con, tickers, media):
    hoje = datetime.now().strftime("%Y%m%d")
    cols = [c[0] for c in con.execute(f"DESCRIBE {L.TABELA}").fetchall()]
    if "liquidez_desde" not in cols:
        raise SystemExit("Corra antes o Liquidez_opc.py --forcar (cria as colunas liquidez_base / liquidez_desde).")
    linhas, hist = [], []
    for t in tickers:
        r = L.ler_ticker(t)
        classe = "Media" if t in media else "Alta"
        linhas.append(dict(ticker=t, liquidez_classificacao=classe, liquidez_base=classe, liquidez_desde=hoje, tem_opcoes=True,
                           qtd_vencimentos_analisados=L.QTD_VENCIMENTOS, data_atualizacao=f"{hoje}_incluir", erro=0,
                           data_reclassificacao=hoje, score_mediana=r["score_liquidez"], dias_amostra=1, **r))
        hist.append(dict(data=hoje, ticker=t, data_hora=datetime.now().strftime("%Y-%m-%d %H:%M"), erro=None, **r))
        print(f"{t}: {classe} (volume {r['volume_total_opcoes']:,}, score {r['score_liquidez']:,.0f})")
    df = pd.DataFrame(linhas).reindex(columns=cols)
    con.register("novos_liq", df)
    con.execute(f"DELETE FROM {L.TABELA} WHERE trim(ticker) IN (SELECT ticker FROM novos_liq)")
    con.execute(f"INSERT INTO {L.TABELA} BY NAME SELECT * FROM novos_liq")
    con.unregister("novos_liq")
    h = pd.DataFrame(hist).reindex(columns=[c[0] for c in con.execute(f"DESCRIBE {L.TABELA_HIST}").fetchall()])
    con.register("novos_hist", h)
    con.execute(f"DELETE FROM {L.TABELA_HIST} WHERE data = ? AND ticker IN (SELECT ticker FROM novos_hist)", [hoje])
    con.execute(f"INSERT INTO {L.TABELA_HIST} BY NAME SELECT * FROM novos_hist")
    con.unregister("novos_hist")
    L.sincronizar_sqlite(con)


def main():
    args = sys.argv[1:]
    media = {a.strip().upper() for a in args[args.index("--media") + 1:]} if "--media" in args else set()
    tickers = list(dict.fromkeys(a.strip().upper() for a in args if a != "--media"))  # sem repetidos
    if not tickers:
        raise SystemExit(__doc__)
    con = duckdb.connect(L.DB_FILE, config={"memory_limit": "2GB"})
    try:
        carregar_historico(con, tickers)
        gravar_liquidez(con, tickers, media)
        print(con.execute(f"SELECT liquidez_classificacao, count(*) FROM {L.TABELA} GROUP BY 1 ORDER BY 1").fetchall())
    finally:
        con.close()


if __name__ == "__main__":
    main()
