"""推送层：token 从哪来、什么时候不该发、发出去了才记账。

推送是整条链路里唯一"会打扰到人"的一层，所以这里重点盯三件事：

  1. token 没配好时要给出人话提示，不能抛异常、也不能假装发成功了；
  2. 同一天的简报默认只推一次（周末、节假日重跑日终不会重复吵人）；
  3. 发失败时本地一个字都不能改，更不能把"已推送"记上。

测试全部离线：真正发 HTTP 的 notify._post 会被替换掉。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import db, notify
from collector.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUCCESS = {"code": 200, "msg": "请求成功", "data": "push-id"}
TRADE_DATE = "2026-09-18"


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)

        # project_root 指到临时目录：token 文件、推送状态都落在那里，
        # 不会碰到这台机器上真在用的 token。
        self.cfg = load_config(PROJECT_ROOT / "config.yaml", project_root=root)
        self.cfg["notify"]["pushplus"]["enabled"] = True
        self.cfg["_db_path"] = str(root / "t.db")

        self.conn = db.connect(self.cfg["_db_path"])
        db.init_db(self.conn, PROJECT_ROOT / "schema.sql")
        self.addCleanup(self.conn.close)

        self.token_file = root / "data" / "pushplus.token"
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file = root / "data" / "last-push.txt"
        self._no_env = mock.patch.dict(os.environ, {"ASHARE_PUSHPLUS_TOKEN": ""})
        self._no_env.start()
        self.addCleanup(self._no_env.stop)

    # ---- 夹具 ----

    def _seed(self, trade_date: str = TRADE_DATE, alerts: int = 1) -> None:
        db.upsert_rows(
            self.conn,
            "features_daily",
            [{
                "code": "SH600487",
                "trade_date": trade_date,
                "state": "range",
                "state_days": 14,
                "trend_score": 52.97,
                "opportunity_score": 0.5,
                "data_quality_flag": "ok",
                "updated_at": db.now_iso(),
            }],
            ["code", "trade_date"],
        )
        for index in range(alerts):
            db.upsert_rows(
                self.conn,
                "alerts",
                [{
                    "created_at": db.now_iso(),
                    "trade_date": trade_date,
                    "code": "SH600487",
                    "level": "P1",
                    "signal_type": f"TEST_SIGNAL_{index}",
                    "message": "价格进入支撑带",
                }],
                ["code", "trade_date", "signal_type", "level"],
            )

    def _write_token(self, text: str = "file-token") -> None:
        self.token_file.write_text(text + "\n", encoding="utf-8")

    def _push(self, **kwargs):
        with mock.patch.object(notify, "_post", return_value=SUCCESS) as post:
            result = notify.push_daily(self.conn, self.cfg, **kwargs)
        return result, post

    # ---- token 从哪来 ----

    def test_token_priority_config_then_env_then_file(self):
        self._write_token("file-token")
        self.assertEqual(notify.resolve_token(self.cfg)[0], "file-token")

        with mock.patch.dict(os.environ, {"ASHARE_PUSHPLUS_TOKEN": "env-token"}):
            self.assertEqual(notify.resolve_token(self.cfg)[0], "env-token")

            self.cfg["notify"]["pushplus"]["token"] = "config-token"
            self.assertEqual(notify.resolve_token(self.cfg)[0], "config-token")

    def test_token_file_accepts_key_value_and_comments(self):
        self._write_token("# 推送 token\nPUSHPLUS_TOKEN = 'kv-token'")
        self.assertEqual(notify.resolve_token(self.cfg)[0], "kv-token")

    def test_missing_token_tells_where_to_put_it(self):
        self._seed()
        result, post = self._push()
        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertIn("data/pushplus.token", result["note"])
        post.assert_not_called()

    def test_disabled_means_no_push_at_all(self):
        self.cfg["notify"]["pushplus"]["enabled"] = False
        self._write_token()
        self._seed()
        result, post = self._push()
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertIn("enabled", result["note"])
        post.assert_not_called()

    # ---- 内容 ----

    def test_title_carries_alert_count(self):
        self._seed(alerts=2)
        message = notify.build_message(self.conn, self.cfg, TRADE_DATE)
        self.assertIn(TRADE_DATE, message["title"])
        self.assertIn("2 条提醒", message["title"])
        self.assertIn("价格进入支撑带", message["content"])

    def test_quiet_day_says_no_alert(self):
        self._seed(alerts=0)
        message = notify.build_message(self.conn, self.cfg, TRADE_DATE)
        self.assertIn("无提醒", message["title"])

    def test_long_content_is_truncated(self):
        self._seed()
        self.cfg["notify"]["pushplus"]["max_chars"] = 60
        message = notify.build_message(self.conn, self.cfg, TRADE_DATE)
        self.assertLessEqual(len(message["content"]), 60)
        self.assertIn("截断", message["content"])

    def test_no_features_yet_is_a_readable_error(self):
        with self.assertRaises(notify.PushError):
            notify.build_message(self.conn, self.cfg, None)

    # ---- 发送与记账 ----

    def test_success_marks_state_and_alerts(self):
        self._write_token()
        self._seed()
        result, post = self._push()
        self.assertTrue(result["ok"], result["note"])
        self.assertFalse(result["skipped"])
        post.assert_called_once()
        payload = post.call_args[0][1]
        self.assertEqual(payload["token"], "file-token")
        self.assertEqual(payload["template"], "txt")
        self.assertEqual(notify.last_pushed(self.cfg), TRADE_DATE)
        row = db.query_one(self.conn, "SELECT notified_at FROM alerts WHERE trade_date=?", (TRADE_DATE,))
        self.assertTrue(row["notified_at"])

    def test_same_day_is_pushed_only_once(self):
        self._write_token()
        self._seed()
        first, _ = self._push()
        second, post = self._push()
        self.assertTrue(first["ok"])
        self.assertTrue(second["skipped"])
        self.assertIn("已经推过", second["note"])
        post.assert_not_called()

    def test_force_resends(self):
        self._write_token()
        self._seed()
        self._push()
        again, post = self._push(force=True)
        self.assertTrue(again["ok"])
        self.assertFalse(again["skipped"])
        post.assert_called_once()

    def test_dry_run_sends_nothing_and_keeps_state_clean(self):
        self._write_token()
        self._seed()
        result, post = self._push(dry_run=True)
        self.assertTrue(result["skipped"])
        post.assert_not_called()
        self.assertEqual(notify.last_pushed(self.cfg), "")

    def test_only_on_alerts_keeps_quiet_days_silent(self):
        self.cfg["notify"]["pushplus"]["only_on_alerts"] = True
        self._write_token()
        self._seed(alerts=0)
        result, post = self._push(force=True)
        self.assertTrue(result["skipped"])
        self.assertIn("沉默", result["note"])
        post.assert_not_called()

    def test_failure_keeps_state_clean_and_says_why(self):
        self._write_token()
        self._seed()
        with mock.patch.object(notify, "_post", side_effect=notify.PushError("连不上 PushPlus：超时")):
            result = notify.push_daily(self.conn, self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("推送失败", result["note"])
        self.assertIn("超时", result["note"])
        self.assertEqual(notify.last_pushed(self.cfg), "")
        row = db.query_one(self.conn, "SELECT notified_at FROM alerts WHERE trade_date=?", (TRADE_DATE,))
        self.assertIsNone(row["notified_at"])

    def test_api_rejection_is_not_a_success(self):
        self._write_token()
        self._seed()
        rejected = {"code": 500, "msg": "token 无效"}
        with mock.patch.object(notify, "_post", return_value=rejected):
            result = notify.push_daily(self.conn, self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("token 无效", result["note"])

    def test_non_json_answer_becomes_a_readable_error(self):
        self.assertTrue(hasattr(notify, "PushError"))
        with mock.patch.object(notify.urllib.request, "urlopen", side_effect=OSError("boom")):
            with self.assertRaises(notify.PushError):
                notify._post(notify.API_URL, {"token": "x"}, 1.0)

    # ---- 测试消息 ----

    def test_test_message_reports_token_source(self):
        self._write_token("file-token")
        with mock.patch.object(notify, "_post", return_value=SUCCESS):
            result = notify.send_test(self.cfg)
        self.assertTrue(result["ok"], result["note"])
        self.assertIn("data/pushplus.token", result["content"])

    def test_test_message_without_token_is_readable(self):
        result = notify.send_test(self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("token", result["note"])


if __name__ == "__main__":
    unittest.main()
