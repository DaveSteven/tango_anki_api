# Tango Anki API

FastAPI + PostgreSQL 后端，用于保存浏览器设备的单词复习状态、每日进度和学习设置。

```bash
docker compose up --build -d
curl http://localhost:8002/health
```

- API: `http://localhost:8002`
- Swagger: `http://localhost:8002/docs`
- PostgreSQL: `localhost:5433`

前端首次连接后会自动把现有 localStorage 数据迁移到数据库。本地开发默认使用 `david-local` 配置，可通过 `VITE_DEVICE_ID` 覆盖。

导入 localStorage 导出的 review JSON：

```bash
python3 scripts/import_reviews.py /path/to/reviews.json
```
