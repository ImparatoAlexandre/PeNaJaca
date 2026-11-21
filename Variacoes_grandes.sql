-- Cria a view Variacoes_grandes (DuckDB, C:\BD\Stocks.duckdb). Corre uma vez, a view acompanha a Stocks_Ind.
-- Eventos: dias em que um ticker com opcoes liquidas (Stocks_OPC_liquidez = Alta / Muito alta) fechou com variacao
--          entre 7% e 10% (alta ou baixa) face ao close do pregao anterior, nos ultimos 3 meses da Stocks_Ind.
-- Cada evento traz 4 linhas: os 3 pregoes anteriores (dia_rel -3, -2, -1) e o proprio dia (dia_rel 0),
-- com todas as colunas da Stocks_Ind de cada dia.
-- evento:          numero do evento (as 4 linhas do mesmo evento tem o mesmo numero; 1 = mais recente)
-- Data_operacao:   dia de referencia (dia da variacao de 7% a 10%), igual nas 4 linhas
-- variacao_evento: variacao % do close no Data_operacao
-- dia_rel:         pregao relativo ao Data_operacao (0 = o proprio dia); pregoes da Stocks_Ind, nao dias de calendario
-- data:            data da linha
-- variacao_dia:    variacao % do close dessa linha face ao pregao anterior
-- Exemplo: SELECT * FROM Variacoes_grandes WHERE ticker = 'CRWD' ORDER BY evento, dia_rel

DROP VIEW IF EXISTS Variacoes_grandes;

CREATE VIEW Variacoes_grandes AS
WITH s AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY trim(ticker) ORDER BY data) AS rn,
        ROUND(100 * (close / LAG(close) OVER (PARTITION BY trim(ticker) ORDER BY data) - 1), 2) AS variacao_dia
    FROM Stocks_Ind
), liq AS (
    SELECT trim(ticker) AS ticker, MAX(liquidez_classificacao) AS liquidez
    FROM Stocks_OPC_liquidez
    WHERE liquidez_classificacao IN ('Alta', 'Muito alta')
    GROUP BY 1
), eventos AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY s.data DESC, ABS(s.variacao_dia) DESC, trim(s.ticker)) AS evento,
        trim(s.ticker) AS ticker,
        s.data AS data_oper,
        s.variacao_dia AS variacao_evento,
        liq.liquidez,
        s.rn AS rn0
    FROM s
    JOIN liq ON liq.ticker = trim(s.ticker)
    WHERE strptime(s.data, '%Y%m%d')::DATE >= (SELECT strptime(MAX(data), '%Y%m%d')::DATE FROM Stocks_Ind) - INTERVAL 3 MONTH
      AND ABS(s.variacao_dia) BETWEEN 7 AND 10
)
SELECT
    e.evento,
    strptime(e.data_oper, '%Y%m%d')::DATE AS Data_operacao,
    e.ticker,
    e.variacao_evento,
    e.liquidez,
    s.rn - e.rn0 AS dia_rel,
    strptime(s.data, '%Y%m%d')::DATE AS "data",
    s.variacao_dia,
    s.* EXCLUDE (ticker, data, rn, variacao_dia)
FROM eventos e
JOIN s ON trim(s.ticker) = e.ticker AND s.rn BETWEEN e.rn0 - 3 AND e.rn0
ORDER BY e.evento, dia_rel;
