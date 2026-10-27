# 云端部署

当前部署：Ubuntu 云主机 124.223.189.242，应用与 SQLite 数据位于云端；文本使用线上 API，Windows ComfyUI 暂不接入。

- 应用目录：`/home/ubuntu/Yingzhang`，与 `/home/ubuntu/Kazumi` 同级
- 数据目录：`/home/ubuntu/Yingzhang/data`
- 兼容路径：`/opt/yingzhang`、`/var/lib/yingzhang`、`/etc/yingzhang` 分别链接至新应用、数据和配置目录，保持虚拟环境与服务引用有效
- 执行环境：Python 3.11，按 uv.lock 安装
- 服务：`yingzhang-design`（网页）、`yingzhang-design-worker`（文本执行器）。旧的 `yingzhang-web`、`yingzhang-worker` 已停用
- 界面地址：http://124.223.189.242:8501
- 环境配置：`/home/ubuntu/Yingzhang/config/runtime.env`，root 可读，不随项目上传

## 从另一台电脑打开

在那台电脑的浏览器打开：

http://124.223.189.242:8501

页面和数据库都在云服务器上。工作台没有登录页，知道这个地址的人都能打开，云主机安全组需放行 TCP 8501。

## 配置线上 API

SSH 登录后运行：

```bash
sudoedit /etc/yingzhang/runtime.env
```

填写 `NOVEL_LLM_API_KEY` 的真实值，保存后重启：

```bash
sudo systemctl restart yingzhang-design yingzhang-design-worker
```

再在工作台的「模型配置」填写供应商地址、模型，并粘贴 API Key。显式连接测试会产生小额真实请求；部署验证不进行付费调用。

## 运维

```bash
sudo systemctl status yingzhang-design yingzhang-design-worker
sudo journalctl -u yingzhang-design -u yingzhang-design-worker -n 50 --no-pager
```

业务库通过 Alembic 迁移。备份使用 SQLite backup API，不在运行中只复制主数据库文件。升级与重启前先查看是否有运行中的文本任务，避免中断已付费请求。

不从开发机复制已有小说数据、检查点、环境或密钥。首次部署使用独立的新数据库；界面与执行器重启不会删除数据。
