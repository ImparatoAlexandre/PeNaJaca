-- Resultado por mes por modelo (tabelas Evolucao_call_put = etapa1 e Evolucao_vol_call_put = etapa2)
--   etapa1:   compra call delta 0.30 e put delta -0.30 (1 call + 1 put, retorno do par), ext ordem 1, IV <= 75% por perna, ±7%, corte D7
--   etapa2:   igual, ext ordem 1-3 (par de menor vol do dia), IV <= 80% por perna, ±10%, corte D5
-- Mes = mes da data da operacao. So operacoes fechadas.
-- soma_todos        soma dos retornos de todos os sinais do mes
-- lucro_5000        etapa1 e etapa2: lucro do mes com 5000 por operacao (capital unico, lucro retirado)
-- Funciona no SQLite do Metabase (3.35+ com funcoes matematicas) e na DuckDB.

WITH base AS (
    SELECT 'etapa1' AS estrategia, substr(dataoperacao, 1, 6) AS mes, status, retorno, selecionada_capital_unico AS cu
    FROM Evolucao_call_put
    UNION ALL
    SELECT 'etapa2', substr(dataoperacao, 1, 6), status, retorno, selecionada_capital_unico
    FROM Evolucao_vol_call_put
),
fechadas AS (
    SELECT * FROM base WHERE status IN ('alvo', 'corte', 'vencimento', 'alvo_alta', 'alvo_baixa')
)
SELECT
    estrategia,
    mes,
    COUNT(*)                                                                    AS sinais,
    SUM(CASE WHEN status = 'alvo' THEN 1 ELSE 0 END)                            AS alvo,
    SUM(CASE WHEN status = 'alvo_alta' THEN 1 ELSE 0 END)                       AS alvo_alta,
    SUM(CASE WHEN status = 'alvo_baixa' THEN 1 ELSE 0 END)                      AS alvo_baixa,
    SUM(CASE WHEN status = 'corte' THEN 1 ELSE 0 END)                           AS corte,
    SUM(CASE WHEN status = 'vencimento' THEN 1 ELSE 0 END)                      AS vencimento,
    ROUND(100.0 * SUM(CASE WHEN retorno > 0 THEN 1 ELSE 0 END) / COUNT(*), 0)   AS acerto_pct,
    ROUND(AVG(retorno), 2)                                                      AS media_pct,
    ROUND(MIN(retorno), 2)                                                      AS pior_pct,
    ROUND(SUM(retorno), 2)                                                      AS soma_todos_pct,
    SUM(CASE WHEN cu = 1 THEN 1 ELSE 0 END)                                     AS ops_capital_unico,
    ROUND(SUM(CASE WHEN cu = 1 THEN retorno ELSE 0 END), 2)                     AS capital_unico_soma_pct,
    ROUND(SUM(CASE WHEN cu = 1 THEN 5000 * retorno / 100.0 ELSE 0 END), 2)     AS lucro_5000
FROM fechadas
GROUP BY estrategia, mes
ORDER BY mes, estrategia;
