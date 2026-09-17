"""打包数据库：拷到另一台电脑之前，得先确认库是一致的、能解出来。"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from collector import db, warehouse
from collector.config import load_config
from tests.test_server import CONFIG_SAMPLE, _seed_db


class PackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cfg_path = self.root / "config.yaml"
        cfg_path.write_text(CONFIG_SAMPLE, encoding="utf-8")
        self.cfg = load_config(cfg_path, project_root=self.root)
        self.cfg["_db_path"] = str(self.root / "market.db")
        self.cfg["_config_path"] = str(cfg_path)
        _seed_db(Path(self.cfg["_db_path"]))

    def tearDown(self):
        self.tmp.cleanup()

    def test_pack_creates_zip_with_database_and_readme(self):
        out = self.root / "备份"
        result = warehouse.pack_database(self.cfg, out_dir=out, verbose=False)
        self.assertTrue(result["ok"], result.get("message"))
        target = Path(result["path"])
        self.assertTrue(target.exists())
        self.assertEqual(target.suffix, ".zip")
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            self.assertIn("data/market.db", names)     # 解压出来就能直接放回 data/
            self.assertIn("README.txt", names)         # 说明放在包里，免得拷过去忘了怎么用
            note = archive.read("README.txt").decode("utf-8")
        self.assertIn("怎么用", note)
        self.assertIn("完整性检查：ok", note)
        self.assertEqual(result["stats"]["bars"], 2)   # 种子库里两根日线

    def test_pack_can_copy_without_compression(self):
        result = warehouse.pack_database(self.cfg, out_dir=self.root / "备份",
                                         compress=False, verbose=False)
        self.assertTrue(result["ok"])
        self.assertEqual(Path(result["path"]).suffix, ".db")

    def test_pack_reports_missing_database(self):
        cfg = dict(self.cfg)
        cfg["_db_path"] = str(self.root / "nope.db")
        result = warehouse.pack_database(cfg, verbose=False)
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["message"])


if __name__ == "__main__":
    unittest.main()
