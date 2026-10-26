# 映章项目部署文件

提交前请确认：不包含 `data/`、`.env`、运行数据库、API Key、SSH 私钥或服务器配置密钥。

```bash
cd /home/ubuntu/Yingzhang
# 服务器更新建议：先备份数据库，再拉取、同步依赖、迁移、重启
sudo systemctl stop yingzhang-worker yingzhang-web
git pull --ff-only origin main
uv sync --frozen
.venv/bin/alembic upgrade head
sudo systemctl start yingzhang-web yingzhang-worker
```

真实环境配置保留在服务器 `/home/ubuntu/Yingzhang/config/runtime.env`，不要提交到仓库。
