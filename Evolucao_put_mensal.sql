-- Resultado por mes por modelo (tabelas Evolucao_stock e Evolucao_call_put)
--   put_30:   ativo + put delta -0.30, alvo +10%, stop -4% no ativo, sem corte (fica ate ao vencimento da put)
--   stock:    so o ativo (sem put), mesmos sinais, stop e alvo da put_30, sem stop nem alvo vende no vencimento que a put teria
--   call_put: compra call delta 0.30 e put delta -0.30 (1 call + 1 put, retorno do par), so sinais ext, sai quando o ativo toca +10% ou -10%
-- Mes = mes da data da operacao. So operacoes fechadas.
-- soma_todos        soma dos retornos de todos os sinais do mes
-- capital_unico_%   put_30 e stock: retorno composto do mes com o capital todo numa operacao de cada vez
--                   (exp(sum(ln(1 + r)))); se o Metabase nao tiver ln/exp, use capital_unico_soma_%
-- lucro_5000        call_put: lucro do mes com 5000 por operacao (capital unico, lucro retirado)
-- Funciona no SQLite do Metabase (3.35+ com funcoes matematicas) e na DuckDB.

WITH base AS (
    SELECT 'put_30' AS estrategia, substr(dataoperacao, 1, 6) AS mes, status, retorno, selecionada_capital_unico AS cu
    FROM Evolucao_stock
    UNION ALL
    SELECT 'stock', substr(dataoperacao, 1, 6), status, ROUND((saida_ativo / close_oper - 1) * 100, 2), selecionada_capital_unico
    FROM Evolucao_stock
    UNION ALL
    SELECT 'call_put', substr(dataoperacao, 1, 6), status, retorno, selecionada_capital_unico
    FROM Evolucao_call_put
),
fechadas AS (
    SELECT * FROM base WHERE status IN ('alvo', 'corte', 'vencimento', 'stop', 'alvo_alta', 'alvo_baixa')
)
SELECT
    estrategia,
    mes,
    COUNT(*)                                                                    AS sinais,
    SUM(CASE WHEN status = 'alvo' THEN 1 ELSE 0 END)                            AS alvo,
    SUM(CASE WHEN status = 'alvo_alta' THEN 1 ELSE 0 END)                       AS alvo_alta,
    SUM(CASE WHEN status = 'alvo_baixa' THEN 1 ELSE 0 END)                      AS alvo_baixa,
    SUM(CASE WHEN status = 'stop' THEN 1 ELSE 0 END)                            AS stop,
    SUM(CASE WHEN status = 'vencimento' THEN 1 ELSE 0 END)                      AS vencimento,
    ROUND(100.0 * SUM(CASE WHEN retorno > 0 THEN 1 ELSE 0 END) / COUNT(*), 0)   AS acerto_pct,
    ROUND(AVG(retorno), 2)                                                      AS media_pct,
    ROUND(MIN(retorno), 2)                                                      AS pior_pct,
    ROUND(SUM(retorno), 2)                                                      AS soma_todos_pct,
    SUM(CASE WHEN cu = 1 THEN 1 ELSE 0 END)                                     AS ops_capital_unico,
    ROUND(SUM(CASE WHEN cu = 1 THEN retorno ELSE 0 END), 2)                     AS capital_unico_soma_pct,
    CASE WHEN estrategia <> 'call_put' THEN
        ROUND(100.0 * (EXP(SUM(CASE WHEN cu = 1 THEN LN(1 + retorno / 100.0) ELSE 0 END)) - 1), 2) END AS capital_unico_pct,
    CASE WHEN estrategia = 'call_put' THEN
        ROUND(SUM(CASE WHEN cu = 1 THEN 5000 * retorno / 100.0 ELSE 0 END), 2) END                       AS lucro_5000
FROM fechadas
GROUP BY estrategia, mes
ORDER BY mes, estrategia;
