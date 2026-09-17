-- ============================================================
-- 被仲裁压制的信号：系统沉默或降级的记录
-- 价值：事后能判断"当时那条规则压对了没有"
-- ============================================================

-- 按规则统计：哪条规则最常出手
SELECT rule_applied   AS 规则,
       decision       AS 裁决,
       COUNT(*)       AS 次数
FROM arbitration_log
GROUP BY rule_applied, decision
ORDER BY 次数 DESC;

-- 明细：什么时候压了谁
SELECT trade_date        AS 交易日,
       code              AS 标的,
       party_a           AS 信号,
       party_b           AS 对方,
       rule_applied      AS 规则,
       suppressed_signal AS 被压制的说明
FROM arbitration_log
ORDER BY trade_date DESC, id DESC
LIMIT 30;

-- 被压制信号的后续表现（需先跑 10-提醒表现回填.sql 才会有人）
SELECT code        AS 标的,
       trade_date  AS 交易日,
       rule_applied AS 压制规则,
       ROUND(outcome_20d, 2) AS 二十日后涨跌幅
FROM arbitration_log
WHERE outcome_20d IS NOT NULL
ORDER BY outcome_20d DESC;
