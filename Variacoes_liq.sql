-- Cria a view Variacoes_liq. SQL igual para DuckDB (C:\BD\Stocks.duckdb) e SQLite do Metabase (C:\Metabase\data\visualizacao.sqlite).
-- Corre uma vez em cada base, a view acompanha a Stocks_Ind.
-- Tickers com opcoes liquidas (Stocks_OPC_liquidez = Alta / Muito alta) com grande chance de movimento forte nos pregoes seguintes:
--   amp_med20 >= 8:          media de 20 pregoes da amplitude do candle (Max - Min) / close do pregao anterior, em %
--   amp_med5 >= amp_med20:   amplitude media dos ultimos 5 pregoes acima da de 20 (volatilidade a aumentar)
--   |dist_MM50%| > 25:       longe da MM50, em queda ou em alta
-- 2026-11-21: amp_med20 >= 8 (era 6) para alvo de 11%. Historico mai/25-nov/26: ~87% tocam +-11% (intradiario, sobre o close
--             do sinal) nos 5 pregoes seguintes e ~93% nos 7; ~1.2 sinais por dia, 43% dos dias sem sinal
--             (com amp_med20 >= 6: ~94% tocam +-8% em 5 pregoes, mas so ~80% tocam +-11%).
-- liquidez: classificacao atual da Stocks_OPC_liquidez (nao a da data do sinal)
-- data: texto AAAA-MM-DD
-- Exemplo: SELECT * FROM Variacoes_liq ORDER BY data DESC, ticker

DROP VIEW IF EXISTS Variacoes_liq;

CREATE VIEW Variacoes_liq AS
WITH liq AS (
    SELECT trim(ticker) AS ticker, MAX(liquidez_classificacao) AS liquidez
    FROM Stocks_OPC_liquidez
    WHERE liquidez_classificacao IN ('Alta', 'Muito alta')
    GROUP BY trim(ticker)
), s AS (
    SELECT
        trim(si.ticker) AS ticker,
        si.data,
        si."dist_MM50%" AS dist_mm50,
        (si.Max - si.Min) / LAG(si.close) OVER (PARTITION BY trim(si.ticker) ORDER BY si.data) * 100 AS amp
    FROM Stocks_Ind si
    JOIN liq ON liq.ticker = trim(si.ticker)
), m AS (
    SELECT
        ticker, data, dist_mm50,
        AVG(amp) OVER w20 AS amp_med20,
        COUNT(amp) OVER w20 AS n20,
        AVG(amp) OVER (PARTITION BY ticker ORDER BY data ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS amp_med5
    FROM s
    WINDOW w20 AS (PARTITION BY ticker ORDER BY data ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
)
SELECT
    substr(m.data, 1, 4) || '-' || substr(m.data, 5, 2) || '-' || substr(m.data, 7, 2) AS data,
    m.ticker,
    liq.liquidez
FROM m
JOIN liq ON liq.ticker = m.ticker
WHERE m.n20 = 20
  AND m.amp_med20 >= 8
  AND m.amp_med5 >= m.amp_med20
  AND ABS(m.dist_mm50) > 25
ORDER BY 1 DESC, 2;
