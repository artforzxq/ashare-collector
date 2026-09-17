-- ============================================================
-- 提醒：按级别与类型统计 + 最近明细
-- ============================================================

-- 按级别与信号类型统计
SELECT level         AS 级别,
       signal_type   AS 信号类型,
       COUNT(*)      AS 次数,
       MIN(trade_date) AS 首次,
       MAX(trade_date) AS 最近
FROM alerts
GROUP BY level, signal_type
ORDER BY level, 次数 DESC;

-- 最近 30 条提醒明细
SELECT trade_date   AS 交易日,
       code         AS 标的,
       level        AS 级别,
       signal_type  AS 信号类型,
       ROUND(price, 3) AS 价格,
       message      AS 说明,
       arbitrated_by AS 放行规则
FROM alerts
ORDER BY trade_date DESC, id DESC
LIMIT 30;

-- 每天提醒条数（看提醒预算有没有被用满）
SELECT trade_date AS 交易日,
       SUM(CASE WHEN level = 'P0' THEN 1 ELSE 0 END) AS P0条数,
       SUM(CASE WHEN level = 'P1' THEN 1 ELSE 0 END) AS P1条数,
       SUM(CASE WHEN level = 'P2' THEN 1 ELSE 0 END) AS P2条数
FROM alerts
GROUP BY trade_date
ORDER BY trade_date DESC;
