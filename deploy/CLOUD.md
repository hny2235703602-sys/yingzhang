# 云端部署

当前部署：Ubuntu 云主机 124.223.189.242，应用与 SQLite 数据位于云端；文本使用线上 API，Windows ComfyUI 暂不接入。

- 应用目录：`/home/ubuntu/Yingzhang`，与 `/home/ubuntu/Kazumi` 同级
- 数据目录：`/home/ubuntu/Yingzhang/data`
- 兼容路径：`/opt/yingzhang`、`/var/lib/yingzhang`、`/etc/yingzhang` 分别链接至新应用、数据和配置目录，保持虚拟环境与服务引用有效
- 执行环境：Python 3.11，按 uv.lock 安装
- 服务：`yingzhang-web`、`yingzhang-worker`
- 界面监听：`127.0.0.1:8501`
- 环境配置：`/home/ubuntu/Yingzhang/config/runtime.env`，root 可读，不随项目上传

## 安全访问

通过 SSH 隧道访问，不依赖新增公网端口：

```bash
ssh -N -L 18501:127.0.0.1:8501 -i ~/.ssh/id_ed25519 ubuntu@124.223.189.242
```

保持该终端运行，在浏览器打开 http://127.0.0.1:18501 。应用及数据库实际运行在云服务器。公网 HTTPS 域名接入可后续增加。

## 配置线上 API

SSH 登录后运行：

```bash
sudoedit /etc/yingzhang/runtime.env
```

填写 `NOVEL_LLM_API_KEY` 的真实值，保存后重启：

```bash
sudo systemctl restart yingzhang-web yingzhang-worker
```

再在工作台的“模型与工作流”配置供应商地址、实际模型 ID，密钥环境变量名称为 `NOVEL_LLM_API_KEY`。显式连接测试会产生小额真实请求；部署验证不进行付费调用。

## 运维

```bash
sudo systemctl status yingzhang-web yingzhang-worker
sudo journalctl -u yingzhang-web -u yingzhang-worker -n 50 --no-pager
```

业务库通过 Alembic 迁移。备份使用 SQLite backup API，不在运行中只复制主数据库文件。升级与重启前先查看是否有运行中的文本任务，避免中断已付费请求。

不从开发机复制已有小说数据、检查点、环境或密钥。首次部署使用独立的新数据库；界面与执行器重启不会删除数据。
