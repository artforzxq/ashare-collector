# Web v2

Vue 3 + TypeScript + Vite + ECharts 前端。旧页面继续位于
`collector/web/index.html`，新版在 Vite 开发入口 `/v2/` 下并行开发。

## 开发

先在仓库根目录启动 Python 服务（macOS / Linux）：

```bash
python run.py web
```

再启动前端：

```bash
cd frontend
npm install
npm run dev
```

Windows 的 cmd / PowerShell 把上面的 `npm` 换成 `npm.cmd`。
`npm install` 只在首次、或 `package.json` / `package-lock.json` 变化后需要。

浏览器访问 <http://127.0.0.1:5174/v2/>。`/api` 会代理到
<http://127.0.0.1:8765>。

生产构建完成后，也可以只启动 Python 服务：

```bash
cd frontend
npm run build
cd ..
python run.py web --no-open
```

然后访问 <http://127.0.0.1:8765/v2/>。旧版页面仍保留在
<http://127.0.0.1:8765/>。

## 检查

```bash
npm test
npm run build
```

构建产物写入 `collector/web/v2/`，该目录作为可再生成文件不提交。
