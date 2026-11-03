# Ubuntu-RBAR 部署

2026-11-03 部署至腾讯云轻量应用服务器 Ubuntu-RBAR，上海地域，实例
`lhins-f2jnvbeo`，访问地址 https://49.234.12.178/。云端为 Ubuntu 26.04、
Python 3.14，2 核 1 GB 内存，已有 2 GB swap。应用在 loopback 8788，
Nginx 在 443 提供 HTTPS 和独立 Basic Auth，只代理已列举的只读 API。
服务器无需 Node.js。

- 当前发布目录为 `/opt/sector-dashboard/releases/20261103-rbar-v1`，`current` 软链指向该版本。
- 应用虚拟环境在 `/opt/sector-dashboard/venv`，数据在 `/var/lib/sector-dashboard/market.sqlite3`。
- `sector-dashboard` 和 `sector-news-worker` 使用独立低权限系统账号，设置资源限制及上海时区，开机自启。
- 模型密钥在 `/etc/sector-dashboard/news.env`，root 0600。认证文件为同目录 `access.htpasswd`，root:www-data 0640。
- Nginx 配置在 `/etc/nginx/sites-available/sector-dashboard`。软件父目录为 0755，认证目录为 root:www-data 0750。
- Let's Encrypt IP 证书使用 shortlived 配置，`sector-cert-renew.timer` 每日检查两次，成功后通过 deploy hook 校验并 reload Nginx。
- 初始数据库压缩备份为 `/var/backups/sector-dashboard/initial-20261103.sqlite3.gz`，仅 root 可读。
- 本机发布清单、校验和、SSH 配置、验收结果在忽略的 `runtime/rbar/` 与 `runtime/deploy/ubuntu-rbar-20261103/` 中。
- 网页登录信息仅保存于本机受限 `runtime/rbar/access.json`，不要提交或复制到公开文档。

本地 126 项测试与生产构建通过。迁移前 SQLite 完整性检查通过，代码和压缩数据库
传输后 SHA-256 一致。公网验收覆盖真实 TLS 校验、未认证 401、认证后的页面与所有 API 类别，
以及非读取方法拒绝。云端新闻六个来源均完成真实采集。

本机切换至只读归档，新闻 launch agent 已 disable。旧网页启动配置备份在
`runtime/rbar/local.fupan.dashboard.before-cloud.plist`。云端和本机数据不自动同步。

运维检查可使用已配置的 SSH 密钥登录 ubuntu，并执行
`sudo systemctl status sector-dashboard sector-news-worker nginx`、
`sudo journalctl -u sector-dashboard -u sector-news-worker --since '-15 minutes'`、
`sudo systemctl list-timers sector-cert-renew.timer`。升级前用 SQLite backup API
导出一致性备份，验证新版本后再切换 current。回滚代码时保留数据库，不覆盖采集后的记录。

腾讯云 zshrc 中的 TECENT_CLOUD_ID/TECENT_CLOUD_SECRET 在本次查询返回
`AuthFailure.SecretIdNotFound`。最终部署使用用户提供的 fupan.pem SSH 密钥，
没有增加临时 SSH 公钥授权。私钥未传往服务器。
