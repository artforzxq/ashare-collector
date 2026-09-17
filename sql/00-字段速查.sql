-- ============================================================
-- 字段速查：不知道某张表有什么字段时先跑这个
-- 把 table_name 改成你要查的表名
-- ============================================================

SELECT column_name  AS 字段,
       data_type    AS 类型,
       is_pk        AS 主键,
       description  AS 说明,
       note         AS 备注
FROM data_dictionary
WHERE table_name = 'alerts'
ORDER BY ordinal;

-- 全部表的字段数量一览
SELECT table_name AS 表名, COUNT(*) AS 字段数
FROM data_dictionary
GROUP BY table_name
ORDER BY 字段数 DESC;

-- 按关键词找字段（比如想知道量能相关字段在哪张表）
SELECT table_name AS 表名, column_name AS 字段, description AS 说明
FROM data_dictionary
WHERE description LIKE '%量能%' OR note LIKE '%量能%' OR description LIKE '%成交额%';
