-- ============================================================
-- 提醒表现回填：给每条提醒补上 5 日 / 20 日后的涨跌幅
-- 注意：这两条 UPDATE 会修改数据，但 daily 任务不会覆盖这两列
-- 跑完 daily 之后定期执行一次即可
-- ============================================================

-- 回填 5 个交易日后的涨跌幅（%）
UPDATE alerts
SET outcome_5d = (
  SELECT ROUND((b2.close / b1.close - 1) * 100, 2)
  FROM bars_daily b1
  JOIN bars_daily b2
    ON b2.code = b1.code
   AND b2.trade_date = (SELECT trade_date FROM bars_daily
                        WHERE code = b1.code AND trade_date > b1.trade_date
                        ORDER BY trade_date LIMIT 1 OFFSET 4)
  WHERE b1.code = alerts.code AND b1.trade_date = alerts.trade_date
)
WHERE outcome_5d IS NULL;

-- 回填 20 个交易日后的涨跌幅（%）
UPDATE alerts
SET outcome_20d = (
  SELECT ROUND((b2.close / b1.close - 1) * 100, 2)
  FROM bars_daily b1
  JOIN bars_daily b2
    ON b2.code = b1.code
   AND b2.trade_date = (SELECT trade_date FROM bars_daily
                        WHERE code = b1.code AND trade_date > b1.trade_date
                        ORDER BY trade_date LIMIT 1 OFFSET 19)
  WHERE b1.code = alerts.code AND b1.trade_date = alerts.trade_date
)
WHERE outcome_20d IS NULL;

-- 胜率统计：哪类信号真的有用（样本够了才有意义）
SELECT level       AS 级别,
       signal_type AS 信号类型,
       COUNT(*)    AS 样本数,
       SUM(CASE WHEN outcome_20d IS NOT NULL THEN 1 ELSE 0 END) AS 已回填数,
       ROUND(AVG(CASE WHEN outcome_20d > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) AS 二十日胜率百分比,
       ROUND(AVG(outcome_20d), 2) AS 二十日平均涨跌幅
FROM alerts
GROUP BY level, signal_type
ORDER BY 级别, 样本数 DESC;

-- 提醒后的逐条明细
SELECT trade_date  AS 交易日,
       code        AS 标的,
       level       AS 级别,
       signal_type AS 信号类型,
       ROUND(outcome_5d, 2)  AS 五日后,
       ROUND(outcome_20d, 2) AS 二十日后
FROM alerts
ORDER BY trade_date DESC, level, code;
