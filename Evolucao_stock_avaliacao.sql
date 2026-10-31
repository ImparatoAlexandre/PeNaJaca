-- Avaliacao da operacao ativo + put com entrada no dia do sinal (tabela Evolucao_stock, gerada por Evolucao_stock.py)
-- Metodo: sinais 'ext' e 'fase2' com ordem_selecao_dia = 1 (coluna estrategia = origem); put delta -0.30;
-- alvo +10%; stop -4% no ativo; sem corte (fica ate ao vencimento da put).
-- Entrada (dia da operacao, ao close): compra o ativo e a put (delta -0.30).
-- Saida: Min do ativo <= close_oper * 0.96 -> vende o ativo e a put (status 'stop'; no mesmo dia do alvo conta o stop);
--        Max do ativo >= close_oper * 1.08 ate ao vencimento -> vende o ativo e a put (status 'alvo');
--        sem stop nem alvo -> liquida ao close do dia do vencimento (status 'vencimento').
-- Funciona no SQLite do Metabase e na DuckDB.
-- Retorno por mes = soma dos retornos / soma dos dias uteis de capital * 21


-- 1) Situacao das operacoes
SELECT status, COUNT(*) AS operacoes
FROM Evolucao_stock
GROUP BY status
ORDER BY operacoes DESC;


-- 2) Resultado global: todos os sinais vs capital unico
SELECT
    CASE WHEN selecionada_capital_unico = 1 THEN 'capital unico' ELSE 'outros sinais' END AS grupo,
    COUNT(*)                                                                 AS fechadas,
    SUM(CASE WHEN status = 'alvo' THEN 1 ELSE 0 END)                         AS no_alvo,
    SUM(CASE WHEN status = 'corte' THEN 1 ELSE 0 END)                        AS no_corte,
    SUM(CASE WHEN status = 'vencimento' THEN 1 ELSE 0 END)                   AS no_vencimento,
    SUM(CASE WHEN status = 'stop' THEN 1 ELSE 0 END)                         AS no_stop,
    ROUND(AVG(retorno), 2)                                                   AS media,
    ROUND(100.0 * SUM(CASE WHEN retorno > 0 THEN 1 ELSE 0 END) / COUNT(*), 0) AS acerto_pct,
    ROUND(MIN(retorno), 2)                                                   AS pior,
    ROUND(AVG(dias_capital), 1)                                              AS dias_medios,
    ROUND(SUM(retorno) / SUM(dias_capital) * 21, 2)                          AS por_mes
FROM Evolucao_stock
WHERE status IN ('alvo', 'corte', 'vencimento', 'stop')
GROUP BY 1

UNION ALL

SELECT
    'todos os sinais',
    COUNT(*),
    SUM(CASE WHEN status = 'alvo' THEN 1 ELSE 0 END),
    SUM(CASE WHEN status = 'corte' THEN 1 ELSE 0 END),
    SUM(CASE WHEN status = 'vencimento' THEN 1 ELSE 0 END),
    ROUND(AVG(retorno), 2),
    ROUND(100.0 * SUM(CASE WHEN retorno > 0 THEN 1 ELSE 0 END) / COUNT(*), 0),
    ROUND(MIN(retorno), 2),
    ROUND(AVG(dias_capital), 1),
    ROUND(SUM(retorno) / SUM(dias_capital) * 21, 2)
FROM Evolucao_stock
WHERE status IN ('alvo', 'corte', 'vencimento', 'stop');


-- 3) Evolucao por mes da operacao
SELECT
    SUBSTR(dataoperacao, 1, 6)                                               AS mes,
    COUNT(*)                                                                 AS fechadas,
    SUM(CASE WHEN status = 'corte' THEN 1 ELSE 0 END)                        AS no_corte,
    SUM(CASE WHEN status = 'vencimento' THEN 1 ELSE 0 END)                   AS no_vencimento,
    SUM(CASE WHEN status = 'stop' THEN 1 ELSE 0 END)                         AS no_stop,
    ROUND(AVG(retorno), 2)                                                   AS media,
    ROUND(SUM(retorno) / SUM(dias_capital) * 21, 2)                          AS por_mes
FROM Evolucao_stock
WHERE status IN ('alvo', 'corte', 'vencimento', 'stop')
GROUP BY 1
ORDER BY 1;


-- 4) Carteira com capital unico, operacao a operacao
SELECT
    dataoperacao, ticker, status, close_oper, alvo_saida, "SAIDA_data", saida_ativo, retorno,
    ROUND(1 + retorno / 100.0, 4) AS fator
FROM Evolucao_stock
WHERE selecionada_capital_unico = 1
ORDER BY dataoperacao;


-- 5) Detalhe das pernas por operacao (entrada e saida)
SELECT
    dataoperacao, ticker, status, close_oper, venc_put,
    strike_put, entrada_put, saida_put,
    entrada_ativo, alvo_saida, saida_ativo,
    "SAIDA_data", retorno
FROM Evolucao_stock
WHERE status IN ('alvo', 'corte', 'vencimento', 'stop', 'aberta')
ORDER BY dataoperacao DESC, ticker;


-- 6) Operacoes sem alvo: cortadas no D+3 ou liquidadas no vencimento (perdas e protecao da put)
SELECT
    dataoperacao, ticker, close_oper, strike_put, valor_put, saida_put,
    status, "SAIDA_data", saida_ativo AS preco_saida, "var_MAX_ativo", retorno
FROM Evolucao_stock
WHERE status IN ('corte', 'vencimento', 'stop')
ORDER BY dataoperacao;


-- 8) Regime de mercado por mes (tabela Mercado_volatilidade, gerada pelo mesmo script)
--    vol_tickers_mediana < 45 = calmo: estrategias de movimento grande (strangle, call comprada) tendem a perder
--    A linha 'ultimos 20 pregoes' mostra o estado atual.
SELECT
    periodo, pregoes, regime, vol_tickers_mediana, vol_mercado, retorno_mercado,
    sinais_ext, subida_5d_mediana, queda_5d_mediana, pct_sobe_4, pct_move_10
FROM Mercado_volatilidade
ORDER BY CASE WHEN periodo = 'ultimos 20 pregoes' THEN 1 ELSE 0 END, periodo;


-- 9) Acompanhamento do ranking pela MM50 e da tendencia do mercado (colunas de oper_abertas)
--    rank_mm50_dia = 1: sinal do dia mais acima da sua MM50 (entre os com opcoes liquidas)
--    mercado_tendencia = 'alta': media dos tickers liquidos com MM10 > MM50 no dia do sinal
--    Com o metodo so de ordem 1, esta consulta mostra so os sinais de ordem 1.
SELECT
    o.rank_mm50_dia,
    o.mercado_tendencia,
    COUNT(*)                                                                  AS operacoes,
    SUM(CASE WHEN c.status = 'alvo' THEN 1 ELSE 0 END)                        AS no_alvo,
    ROUND(100.0 * SUM(CASE WHEN c.status = 'alvo' THEN 1 ELSE 0 END) / COUNT(*), 0) AS pct_alvo,
    ROUND(100.0 * SUM(CASE WHEN c.retorno < 0 THEN 1 ELSE 0 END) / COUNT(*), 0) AS pct_perda,
    ROUND(AVG(c.retorno), 2)                                                  AS media,
    ROUND(MIN(c.retorno), 2)                                                  AS pior
FROM Evolucao_stock c
JOIN oper_abertas o
  ON CAST(o.data_operacao AS VARCHAR) = c.dataoperacao
 AND trim(o.ticker) = c.ticker
 AND lower(trim(o.estrategia)) = 'ext'
 AND o.dia_hist = 1
WHERE c.status IN ('alvo', 'corte', 'vencimento', 'stop')
GROUP BY 1, 2
ORDER BY 1, 2;


-- 10) Resultado por distancia a MM50 e por rank do dia (colunas do proprio Evolucao_stock)
--     Simulacao jan-out: dist >= 20% -> 87% no alvo; abaixo de 0% -> 70%
SELECT
    estrategia,
    CASE
        WHEN "dist_MM50%" IS NULL THEN 'sem MM50'
        WHEN "dist_MM50%" >= 20 THEN '1) >= 20%'
        WHEN "dist_MM50%" >= 5 THEN '2) 5% a 20%'
        WHEN "dist_MM50%" >= 0 THEN '3) 0% a 5%'
        ELSE '4) abaixo da MM50'
    END                                                                       AS faixa_mm50,
    COALESCE(CAST(rank_mm50_dia AS VARCHAR), '-')                             AS rank_mm50,
    COUNT(*)                                                                  AS operacoes,
    ROUND(100.0 * SUM(CASE WHEN status = 'alvo' THEN 1 ELSE 0 END) / COUNT(*), 0) AS pct_alvo,
    ROUND(100.0 * SUM(CASE WHEN retorno < 0 THEN 1 ELSE 0 END) / COUNT(*), 0) AS pct_perda,
    ROUND(AVG(retorno), 2)                                                    AS media,
    ROUND(SUM(retorno) / SUM(dias_capital) * 21, 2)                           AS por_mes
FROM Evolucao_stock
WHERE status IN ('alvo', 'corte', 'vencimento', 'stop')
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;


-- 7) Operacoes sem avaliacao por falta de dados de opcoes (cobertura da recolha)
SELECT status, SUBSTR(dataoperacao, 1, 6) AS mes, COUNT(*) AS operacoes
FROM Evolucao_stock
WHERE status IN ('sem_opcoes', 'sem_put', 'sem_vencimento', 'sem_dados')
GROUP BY 1, 2
ORDER BY 2, 1;
