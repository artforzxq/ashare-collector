# Web v2

Vue 3 + TypeScript + Vite + ECharts 前端。旧页面继续位于
`collector/web/index.html`，新版在 Vite 开发入口 `/v2/` 下并行开发。

## 开发

先在仓库根目录启动 Python 服务：

```powershell
python run.py web
```

再启动前端：

```powershell
cd frontend
npm.cmd install --cache .npm-cache
npm.cmd run dev
```

浏览器访问 <http://127.0.0.1:5174/v2/>。`/api` 会代理到
<http://127.0.0.1:8765>。

## 检查

```powershell
npm.cmd test
npm.cmd run build
```

构建产物写入 `collector/web/v2/`，该目录作为可再生成文件不提交。
