jsonpath-0.82.2-py3-none-any.whl  —— 为什么仓库里会有这个东西
================================================================

akshare 依赖 jsonpath>=0.82，而 jsonpath 只发布源码包（没有官方 wheel），
它的 setup.py 第一行就 `import jsonpath as module`（用自己读版本号）。
pip 构建时会在隔离环境里跑 setup.py，源码目录不在 sys.path 上，于是必然：

    ModuleNotFoundError: No module named 'jsonpath'
    error: metadata-generation-failed

结果是：**只要网络正常、包能下载，akshare 也装不上**。这不是网络问题。

这里的 wheel 是从官方 sdist（jsonpath-0.82.2.tar.gz，PyPI，2023-08-24）构建的，
里面 jsonpath.py **一个字节都没改**（MD5 与原版一致，可以用 zipfile 校验），
只把 setup.py 里那句自引用改成按文件路径加载：

    _spec = importlib.util.spec_from_file_location("jsonpath", pathlib.Path(__file__).with_name("jsonpath.py"))
    module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(module)

装的时候把它和 akshare 一起交给 pip 就行（顺序无所谓，放在命令里即可）：

    python -m pip install "tools\vendor\jsonpath-0.82.2-py3-none-any.whl" akshare baostock

windows\win-run.bat 的安装步骤已经内置了这个兜底：正常装失败时会自动带上这个 wheel 重试一次。

想自己重建（比如换 Python 版本）：
  1. 下载 https://pypi.org/project/jsonpath/ 的 sdist
  2. 解压，按上面的方式改 setup.py
  3. python -m pip wheel --no-deps -w . <解压出来的目录>
