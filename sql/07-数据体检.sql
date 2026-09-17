-- ============================================================
-- 数据体检：采集有没有出问题、哪些数据不可信
-- 数据错了比数据缺了危险，跑完 daily 建议扫一眼这里
-- ============================================================

-- 最近 40 条采集记录
SELECT run_date   AS 运行日,
       source     AS 数据源,
       task       AS 任务,
       status     AS 状态,
       rows       AS 记录数,
       latency_ms AS 耗时毫秒,
       error_msg  AS 说明
FROM data_health
ORDER BY run_date DESC, task
LIMIT 40;

-- 非正常状态的记录
SELECT run_date AS 运行日, task AS 任务, status AS 状态, error_msg AS 说明
FROM data_health
WHERE status <> 'ok'
ORDER BY run_date DESC;

-- 被标记为可疑或阻断的行情
SELECT code        AS 标的,
       trade_date  AS 交易日,
       quality_flag AS 质量标记,
       ROUND(close, 3) AS 收盘价,
       source      AS 来源
FROM bars_daily
WHERE quality_flag <> 'ok'
ORDER BY trade_date DESC;

-- 最新交易日里，观察池中缺数据的标的（应该是空结果）
SELECT i.code AS 缺少数据的标的
FROM instruments i
LEFT JOIN bars_daily b
       ON b.code = i.code
      AND b.trade_date = (SELECT MAX(trade_date) FROM bars_daily)
WHERE b.code IS NULL;
