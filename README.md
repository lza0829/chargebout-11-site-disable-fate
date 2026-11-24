# 充桩条

开会话 → 计量脉冲累加 → 结算关账 → 超时/异常命运。

## 场站下线口径（臂A：以下线后禁新开充为准，全局不混用）

- `POST /api/站点/{id}/下线`：闸关（禁新开）。不是只改启用字——同一事务内校验迁移、
  追加 `site_events`（单调闸序+在充快照）、翻启用字、出回执；重复下线被拒。
- 下线后该站拒绝新开充；**已有会话记脉冲、结算关账照常**（在充收口不冻结）。
- `POST /api/站点/{id}/上线`：闸重开，同样走事件流。
- 闸态物化在 `sites.active`，可由 `site_events` 重放复算：
  `GET /api/站点/{id}/闸` 给出物化闸 vs 重放闸的一致性对照，
  `GET /api/站点/{id}/事件` 即站点审计流水（每条事件即一张回执）。
- 开充/脉冲/结算/下线/上线等写口统一 `BEGIN IMMEDIATE`：
  闸判定与写入同事务串行，并发下线与开充不会产出半截会话。
- 总览可见「禁新开站点 / 下线站在充」；站点页、桩位页、会话册、开充页同刷标记。

```bash
# 测试（夹具覆盖：下线后仍新开、误挡在充关账、并发半截会话、上线后闸态拧花）
cd backend
pip install -r requirements-dev.txt
python -m pytest tests/
```

- 前端 http://127.0.0.1:6500
- API http://127.0.0.1:11500

```powershell
# 本地 API
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 11500

# 本地前端（另开）
cd frontend
npm install
npm run dev
```

或 `docker compose up -d --build`。
