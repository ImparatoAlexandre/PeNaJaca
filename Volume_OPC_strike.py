"""Volume das opcoes por strike no vencimento das operacoes: tabela Volume_OPC_strike (DuckDB e SQLite do Metabase).

Operacoes  linhas da view Evolucao_put_operacoes (modelos stock, call_put, vol_call_put) com dataoperacao >= DESDE;
           cada par (ticker, vencimento = venc_put) entra com a 1.a data de operacao. Novos sinais entram sozinhos
           na rodada seguinte do Operacoes_atualizacao (corre depois dos modelos).
Dados      Call_Put_dados (sem as linhas sinteticas _BS), da 1.a data de operacao ate ao vencimento, todos os strikes
           desse vencimento; uma linha por dia de recolha (DataRegistro, a ultima recolha do dia).
Colunas    pct_volume = % do volume do strike no volume do dia do mesmo ticker, tipo e vencimento (idem pct_oi);
           perna_operacao = modelos cuja operacao usa esse strike (ex.: 'call_put,vol_call_put'), vazio se nenhum.
Termometro por ticker e dia (repetido em todas as linhas do dia; no Metabase usar Maximum, nao Somme): calls + puts
           dos TERMO_VENCIMENTOS vencimentos mais proximos; strike_atm = strike mais proximo do preco; faixa_baixo /
           faixa_cima = strikes com 15% / 85% do volume acumulado (70% centrais, TERMO_FAIXA); centro_volume = 50%;
           pct_abaixo / pct_acima / pct_centro = % desses strikes em relacao ao ATM; largura_70 = pct_acima - pct_abaixo.
           Faixa larga = mercado posicionado para movimento (a IV explica a maior parte; ver memoria do projeto).
Uso        python Volume_OPC_strike.py
"""
import sqlite3

import duckdb


DB_FILE = r"C:\BD\Stocks.duckdb"
SQLITE_FILE = r"C:\Metabase\data\visualizacao.sqlite"
TABELA_DESTINO = "Volume_OPC_strike"
VIEW_OPERACOES = "Evolucao_put_operacoes"
DESDE = "20261001"
TERMO_FAIXA = 0.70        # termometro: faixa de strikes com os 70% centrais do volume
TERMO_VENCIMENTOS = 2     # calls + puts dos 2 vencimentos mais proximos do dia (sem o que vence no proprio dia)


def construir(con):
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {TABELA_DESTINO} AS
        WITH ops AS (
            SELECT ticker, CAST(venc_put AS VARCHAR)[:10] AS vencimento, dataoperacao, modelo, strike_call, strike_put
            FROM {VIEW_OPERACOES}
            WHERE dataoperacao >= ? AND venc_put IS NOT NULL
        ),
        alvos AS (
            SELECT ticker, vencimento, MIN(dataoperacao) AS dataoperacao
            FROM ops GROUP BY ticker, vencimento
        ),
        pernas AS (
            SELECT ticker, vencimento, tipo, strike, string_agg(DISTINCT modelo, ',' ORDER BY modelo) AS perna_operacao
            FROM (
                SELECT ticker, vencimento, 'CALL' AS tipo, strike_call AS strike, modelo FROM ops WHERE strike_call IS NOT NULL
                UNION ALL
                SELECT ticker, vencimento, 'PUT', strike_put, modelo FROM ops WHERE strike_put IS NOT NULL
            )
            GROUP BY ALL
        ),
        dados AS (
            SELECT c.ticker, upper(c.tipo) AS tipo, a.vencimento, c.DataRegistro AS data, a.dataoperacao,
                   MAX(c.precoAtual) AS spot, c.strike, MAX(c.volume) AS volume, MAX(c.openInterest) AS openInterest,
                   MAX(c.lastPrice) AS lastPrice, MAX(c.impliedVolatility) AS iv
            FROM Call_Put_dados c
            JOIN alvos a ON trim(c.ticker) = a.ticker AND CAST(c.dataStrike AS VARCHAR)[:10] = a.vencimento
            WHERE c.ID NOT LIKE '%\\_BS' ESCAPE '\\'
              AND c.DataRegistro >= a.dataoperacao
              AND c.DataRegistro <= replace(a.vencimento, '-', '')
            GROUP BY ALL
        ),
        termo_venc AS (
            SELECT trim(c.ticker) AS ticker, c.DataRegistro AS data, c.strike, c.volume, c.precoAtual,
                   dense_rank() OVER (PARTITION BY trim(c.ticker), c.DataRegistro ORDER BY CAST(c.dataStrike AS VARCHAR)[:10]) AS rv
            FROM Call_Put_dados c
            WHERE trim(c.ticker) IN (SELECT ticker FROM alvos)
              AND c.ID NOT LIKE '%\\_BS' ESCAPE '\\'
              AND c.DataRegistro >= (SELECT MIN(dataoperacao) FROM alvos)
              AND CAST(c.dataStrike AS VARCHAR)[:10] > strftime(strptime(c.DataRegistro, '%Y%m%d'), '%Y-%m-%d')
        ),
        termo_strikes AS (
            SELECT ticker, data, strike, SUM(COALESCE(volume, 0)) AS v, MAX(precoAtual) AS spot
            FROM termo_venc WHERE rv <= {TERMO_VENCIMENTOS} GROUP BY ALL
        ),
        termo_acum AS (
            SELECT *, SUM(v) OVER (PARTITION BY ticker, data ORDER BY strike) / NULLIF(SUM(v) OVER (PARTITION BY ticker, data), 0) AS acum
            FROM termo_strikes
        ),
        termo AS (
            SELECT ticker, data,
                   arg_min(strike, abs(strike - spot)) AS strike_atm,
                   MIN(strike) FILTER (WHERE acum >= {(1 - TERMO_FAIXA) / 2}) AS faixa_baixo,
                   MIN(strike) FILTER (WHERE acum >= 0.5) AS centro_volume,
                   MIN(strike) FILTER (WHERE acum >= {(1 + TERMO_FAIXA) / 2}) AS faixa_cima
            FROM termo_acum GROUP BY ticker, data
        )
        SELECT d.ticker, d.tipo, d.vencimento, d.data, d.dataoperacao, ROUND(d.spot, 2) AS spot, d.strike,
               COALESCE(d.volume, 0) AS volume,
               ROUND(100.0 * COALESCE(d.volume, 0) / NULLIF(SUM(COALESCE(d.volume, 0)) OVER w, 0), 1) AS pct_volume,
               SUM(COALESCE(d.volume, 0)) OVER w AS volume_total,
               COALESCE(d.openInterest, 0) AS openInterest,
               ROUND(100.0 * COALESCE(d.openInterest, 0) / NULLIF(SUM(COALESCE(d.openInterest, 0)) OVER w, 0), 1) AS pct_oi,
               SUM(COALESCE(d.openInterest, 0)) OVER w AS oi_total,
               d.lastPrice, ROUND(d.iv * 100, 1) AS iv,
               p.perna_operacao,
               t.strike_atm, t.faixa_baixo, t.faixa_cima, t.centro_volume,
               ROUND((t.faixa_baixo / t.strike_atm - 1) * 100, 1) AS pct_abaixo,
               ROUND((t.faixa_cima / t.strike_atm - 1) * 100, 1) AS pct_acima,
               ROUND((t.faixa_cima - t.faixa_baixo) / t.strike_atm * 100, 1) AS largura_70,
               ROUND((t.centro_volume / t.strike_atm - 1) * 100, 1) AS pct_centro
        FROM dados d
        LEFT JOIN pernas p ON p.ticker = d.ticker AND p.vencimento = d.vencimento AND p.tipo = d.tipo AND p.strike = d.strike
        LEFT JOIN termo t ON t.ticker = d.ticker AND t.data = d.data
        WINDOW w AS (PARTITION BY d.ticker, d.tipo, d.vencimento, d.data)
        ORDER BY d.ticker, d.vencimento, d.tipo, d.data, d.strike
        """,
        [DESDE],
    )
    return con.execute(f"SELECT * FROM {TABELA_DESTINO}").fetchdf()


def sincronizar_sqlite(df):
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        df.to_sql(TABELA_DESTINO, sq, if_exists="replace", index=False)
    finally:
        sq.close()


def main():
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    try:
        df = construir(con)
    finally:
        con.close()
    sincronizar_sqlite(df)
    resumo = df.groupby(["ticker", "vencimento"]).data.agg(["min", "max", "nunique"])
    print(f"Tabela {TABELA_DESTINO} criada com {len(df)} linhas ({len(resumo)} ticker/vencimento).")
    print(resumo.to_string())


if __name__ == "__main__":
    main()
