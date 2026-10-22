"""启动器一致性：两套编号文件、两个共享启动器、以及 macOS 的执行权限。

为什么要有这一条：启动器是"复制粘贴改出来"的，最容易出的错不是逻辑错，而是
  · Windows 加了 28 号，macOS 忘了加（或者编号错位，28 号双击跑成别的任务）；
  · 新加的 .command 忘了 chmod +x —— 双击时 Finder 走 execve，直接
    "permission denied"，看起来像"文件坏了"，其实是权限位；
  · 共享启动器里 dispatch 的步骤名改了，编号文件还指着旧名字。
这三种错都只在"真去点一下"的时候暴露，所以放在测试里盯住。

注意：执行权限只在 POSIX 上检查。Windows 的 git checkout 不还原 x 位，
在 Windows 上跑这条测试没有意义（os.access 对任何文件都返回 True），所以跳过。
"""

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "windows"
MACOS = ROOT / "macos"

# 只在这边存在、那边用别的方式替代的编号，写在这里而不是让测试放宽。
# 24 = 定时自动运行：macOS 走 launchd（24-自动运行.command），
#      Windows 走"后台-每日任务.bat + 任务计划程序"，所以没有 24 号 .bat。
NUMBERING_EXCEPTIONS = {"24"}

# 24 号自己拼 launchd plist，不是"_mac-run.sh 的薄壳"，所以抓不到步骤名。
# 它调的是 plist 里的 auto 步骤，由下面的 SELF_MANAGED 例外放行。
SELF_MANAGED = {"24"}

STEP_IN_BAT = re.compile(r'win-run\.bat"\s+([a-z][a-z-]*)')
STEP_IN_COMMAND = re.compile(r'_mac-run\.sh"\s+([a-z][a-z-]*)')
BAT_DISPATCH = re.compile(r'"%STEP%"=="([a-z][a-z-]*)"')
SH_CASE_LABEL = re.compile(r'^  ([a-z][a-z-]*)\)\s*$', re.MULTILINE)


def numbered(folder, suffix):
    """{编号: 路径}，只收 "数字-名字" 这种，跳过 _mac-run.sh / win-run.bat。"""
    found = {}
    for path in folder.glob("*" + suffix):
        match = re.match(r"^(\d+)-", path.name)
        if match:
            found[match.group(1)] = path
    return found


def first_step(pattern, path):
    match = pattern.search(path.read_text(encoding="utf-8", errors="replace"))
    return match.group(1) if match else None


class LauncherTests(unittest.TestCase):
    def test_every_windows_step_exists_in_the_dispatcher(self):
        valid = set(BAT_DISPATCH.findall((WINDOWS / "win-run.bat").read_text(encoding="utf-8")))
        self.assertIn("daily", valid)          # 解析本身得是活的，别静默变成空集合
        for number, path in sorted(numbered(WINDOWS, ".bat").items()):
            step = first_step(STEP_IN_BAT, path)
            with self.subTest(bat=path.name):
                self.assertIsNotNone(step, f"{path.name} 没有调用 win-run.bat")
                self.assertIn(step, valid, f"{path.name} 指向的步骤 {step} 在 win-run.bat 里不存在")

    def test_every_mac_step_exists_in_the_dispatcher(self):
        text = (MACOS / "_mac-run.sh").read_text(encoding="utf-8")
        valid = set(SH_CASE_LABEL.findall(text))
        self.assertIn("daily", valid)
        for number, path in sorted(numbered(MACOS, ".command").items()):
            step = first_step(STEP_IN_COMMAND, path)
            with self.subTest(command=path.name):
                if step is None:
                    # 不调共享启动器的，必须是明确登记过的自管文件，
                    # 免得哪天漏写一行调用被当成"它本来就这样"混过去。
                    self.assertIn(number, SELF_MANAGED, f"{path.name} 没有调用 _mac-run.sh")
                    continue
                self.assertIn(step, valid, f"{path.name} 指向的步骤 {step} 在 _mac-run.sh 里不存在")

    def test_both_sides_have_the_same_numbers(self):
        win = set(numbered(WINDOWS, ".bat"))
        mac = set(numbered(MACOS, ".command"))
        self.assertEqual(set(), (win ^ mac) - NUMBERING_EXCEPTIONS,
                         f"两侧编号对不上：Windows 独有 {sorted(win - mac)}，macOS 独有 {sorted(mac - win)}")

    def test_same_number_runs_the_same_job(self):
        win = numbered(WINDOWS, ".bat")
        mac = numbered(MACOS, ".command")
        for number in sorted(set(win) & set(mac)):
            with self.subTest(number=number):
                self.assertEqual(
                    first_step(STEP_IN_BAT, win[number]),
                    first_step(STEP_IN_COMMAND, mac[number]),
                    f"{number} 号在两侧跑的不是同一个任务：{win[number].name} / {mac[number].name}",
                )

    @unittest.skipUnless(os.name == "posix", "执行权限只在 POSIX 上有意义")
    def test_mac_launchers_are_executable(self):
        """双击 .command 时 Finder 是 execve，没有 x 位就 permission denied。"""
        for path in sorted(MACOS.glob("*.command")) + [MACOS / "_mac-run.sh"]:
            with self.subTest(file=path.name):
                self.assertTrue(os.access(path, os.X_OK),
                                f"{path.name} 没有执行权限，双击会报 permission denied（chmod +x 补上）")


if __name__ == "__main__":
    unittest.main()
