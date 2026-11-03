# 东京部署历史

2026-11-02 已迁回本机。东京两个 systemd 服务及其程序、数据库、服务密钥文件和
部署暂存目录已清理。原 `/fupan/` 登录后返回 404，共用监控页面登录后仍返回
200。专属系统账号 `sector-dashboard`（UID 992）随后已删除，并已复核共用监控服务与公网入口。当前运行方式、地址和迁移
备份位置见[项目首页](../README.md)。以下记录用于追溯旧版本。

目标是已有东京 HTTPS 网关的 `/fupan/`，沿用原有认证。后端仍仅监听
`127.0.0.1:8788`，不新增公网端口。静态资源与浏览器 API 使用相对路径，
同时支持本地根路径与网关子路径；网关必须将 `/fupan` 重定向到 `/fupan/`。

2026-11-01 已部署到 `https://43.165.184.144/fupan/`，版本
`20261101-ths-v1`。服务器账号为 `ubuntu`，使用已有项目部署密钥；
原有腾讯云 TAT 通道可用于实例与连接核验。不是默认密钥登录 root。
本任务专用 SSH 配置保存在本机忽略目录 `runtime/deploy/ssh_config`；
凭据不随仓库分发。已有数据库通过 SQLite backup API 导出后导入，
最近交易日为2026-10-30。此后东京与本机数据库独立采集，不自动互相同步。

## 布局

- `/opt/sector-dashboard/releases/<release>/`：版本目录，包含 backend、frontend/dist、requirements.txt 和说明。
- `/opt/sector-dashboard/current`：当前版本软链接。
- `/opt/sector-dashboard/venv`：该应用独立的 Python 3.11+ 环境。
- `/var/lib/sector-dashboard/market.sqlite3`：持久化数据库。
- 版本目录内 `runtime` 软链接到 `/var/lib/sector-dashboard`，供 CLI 使用。
- `sector-dashboard.service`：单 worker（防止重复调度），独立低权限用户，上海时区，开机启动。

首次部署先检查端口、资源与目标目录是否已存在。通过 SQLite backup API
生成本机一致性备份，并运行 `PRAGMA integrity_check`；传输后核对 SHA-256。
**只在远端数据库不存在时导入**，后续版本升级不得覆盖服务器采集的数据。
数据库、登录凭据与虚拟环境不进入源码包或 Git。

安装依赖使用该应用的独立环境和 `requirements.txt`。创建 `sector-dashboard`
系统用户，将持久目录归该用户所有，代码由 root 持有。安装同目录 service；
在现有认证路由内合并 `Caddy.route.example`，不得替换整个网关配置。
先备份网关配置，运行 Caddy validate 后再平滑 reload。
东京网关服务名为 `bitget-monitor-gateway`，运行账号为 `bitget-web`；
原子替换配置时必须保留属主与组，配置权限为 `root:bitget-web 0640`。
仅看到 systemd reload 成功不足以确认生效，还须检查 Caddy 日志及公网新路由。
现有配置备份为 `/etc/bitget-monitor-gateway/Caddyfile.pre-fupan-20261101`。

## 验收与回滚

```sh
systemctl is-active sector-dashboard
systemctl is-enabled sector-dashboard
curl --fail http://127.0.0.1:8788/api/dates
curl --fail 'http://127.0.0.1:8788/api/sectors?kind=concept&date=2026-10-30'
journalctl -u sector-dashboard --since '-10 minutes' --no-pager
```

验证 `/fupan/`、静态资源、API、同花顺概念详情和涨幅榜；未认证的公网请求
应被原网关拒绝。核对源交易日期，不把周末部署日期当成新行情日期。还需从
东京测试公开数据源：部署成功不等于未来交易日采集完整。

升级时记录旧 `current` 目标，保留旧版本和网关备份。代码回滚只切换
`current` 并重启该服务；网关变更需先验证备份再恢复。数据库不随代码回滚。
首次部署失败时停止该服务、恢复网关，保留数据与日志供排查。

## 新闻研究升级（2026-11-02）

首版 `20261102-news-v1` 增加 `/fupan/#news`。使用
`news_upgrade.py` 在服务端执行一致性备份、增量建表、转入小批量验收新闻及
模型用量账本，再切换应用和启用 `sector-news-worker.service`。该脚本是本次
一次性升级入口，检测到已有备份或新闻表非空会停止，不能用于重复覆盖数据。

模型环境文件为 `/etc/sector-dashboard/news.env`，`root:root 0600`，字段
`DEEPSEEK_API_KEY`，仅由 systemd 读取；代码和数据库均不包含密钥。
预算为北京时间每日 $1，超时保留费用预留，采集继续运行。新闻 worker
限制 256 MiB 内存和 50% CPU，独立于行情进程。公网只增加认证内的
`GET/HEAD /fupan/api/news` 与 `/fupan/api/news/*` 路径。

小批量核验 39 个真实事件，涵盖八类题材、四个来源、三类关联；索引见
`news-acceptance-20261102.json`，详细证据随 SQLite 分析修订保存。
本地 81 项测试通过，前端构建通过。新浪财经、CNBC World、NVIDIA 和美联储
实测成功；财联社和东方财富请求失败，状态与可能漏采区间如实显示。
跨语言同事实无法确认时保留独立事件。行业地位不足的公司仅标产业代表。

回滚前先 `systemctl disable --now sector-news-worker`，将 `current` 恢复到
`/opt/sector-dashboard/releases/20261101-ths-v1`，重启 `sector-dashboard`。
网关备份 `/etc/bitget-monitor-gateway/Caddyfile.pre-20261102-news-v1`，恢复时
保留 `root:bitget-web 0640`，先 validate 再 reload。代码回滚保留新增新闻表；
数据库一致性备份为 `/var/lib/sector-dashboard/market.pre-20261102-news-v1.sqlite3`，
不自动覆盖上线后的采集。具体路径另存于同目录 `rollback-20261102-news-v1.json`。

公网验收包括未认证 401、认证 200、POST 404、历史时间隔离、原有板块、
个股榜及 `/`、`/breakout/`、`/maker/`；浏览器通过临时本机认证代理核对东京
实际 `/fupan/` 内容，避免在浏览器地址中放置 Basic Auth 凭据。

## 新闻源与点击验收修复（2026-11-02，news-v2）

该次来源修复版本为 `20261102-news-v2`。财联社旧 `/nodeapi/telegraphList` 返回 404，
已按官网实际脚本改用 `/api/cache?name=telegraph`，当前窗口 20 条。
东方财富因共享请求头误带财联社 Referer 返回 567；现在按来源域名设置
Referer，东方财富窗口 200 条，其余来源不携带无关 Referer。两个 JSON
适配器同时校验供应商成功码；HTTP 失败状态显示具体状态码，错误正文不入库。
仍保留窗口重叠检查与漏采记录，不将恢复后的正常状态当成历史缺口已补齐。

85 项自动化测试通过，包括来源请求头、财联社新结构、供应商错误码、恢复
去重与 HTTP 错误展示。公网仍沿用原认证，旧板块和监控接口保持正常。

东京真实页面通过本机临时认证代理完成浏览器点击验收：事件卡片展开、
股票 002050 筛选、家用电器 BK0456（2026-10-30／修订 258）、同花顺出海50
THS309127（2026-10-30／修订 260），弹层关闭后保留新闻题材与股票筛选。
此前点击未送达的问题在浏览器工具中以完整按下、释放周期解决；未修改业务
点击逻辑。测试代理在验收后关闭，不是生产服务。

本次未改网关和数据库结构。保留旧版本 `20261102-news-v1`，一致性备份为
`/var/lib/sector-dashboard/market.pre-20261102-news-v2.sqlite3`。回滚时停止
news worker，切换 current 到 v1，再重启 `sector-news-worker` 和
`sector-dashboard`；不要覆盖上线后的数据库。

## 事件研究升级（2026-11-02，news-v3）

`20261102-news-v3` 新增 `news_event_links` 与 `news_enrichment` 两张增量表。
合并按相近时间、同一事实和状态保守执行；相似项目、不同报告期、辟谣与确认
不强行合并。新浪括号标题统一提取；明确双语主体组合可合并，其他跨语言
不确定报道保留独立。合并关系带生效时间，历史截止点之前仍读原事件。

重点新闻抓取固定官方/媒体域名、固定文章路径的公开关键段落；检查每次
重定向、公共 IP 并固定连接地址，保留 TLS 主机校验。正文最多 1.5 MB，保存
最多 10 段/6000 字证据而非 HTML 全文；验证或付费限制不绕过。失败按退避最多
尝试3次，成功补证可生成新的分析修订。本地代理虚拟 DNS 地址会被拒绝；
本次美联储补证通过同一模块在东京获取后导入独立本地验证库。

题材支持 AI漫剧、物理AI、TPU、HBM、DDR5 等混排；消费电子和零售贷款不再
因“消费/零售”自动归入消费。通用主营映射仅列为同题材观察名单，不进入事件
股票和领涨排名；业务/产业链正式关联还须有已核验的事件主体与具体关系证据。
直接提及继续校验本地证券目录。暂未扩充原有16家公司业务库。

`news-v3` 分重点研究与普通快讯。重点优先处理，补入实际已存的此前披露，
输出事实引用、基准缺口、条件传导、反证、验证节点和行情时间边界。模型引用
必须存在，解释不得标为原文事实，累计期间不能与单季写成先后变化。
金额或范围不同单独提示，不能自行择数；无基准不声称超预期。旧分析保留，
当前界面提示待升级，历史时点不混入后来分析。每日模型预算仍为1美元。
新增列表筛选 `level=focus|brief`，沿用现有只读接口和网关。

部署前一致性备份：`/var/lib/sector-dashboard/market.pre-20261102-news-v3.sqlite3`。
代码回滚：停止新闻worker，切换 current 到保留的 `20261102-news-v2`，重启
网页与新闻worker；不覆盖数据库。旧版忽略新增表，原始报道和旧分析仍在。

验收：99项自动化测试通过，前端生产构建通过；东京首轮合并214组，四条
真实重点新闻完成新模型验收，美联储和东方财富原文补证成功。财联社个别
文章正文访问受限时保留快讯与缺口，不影响电报采集。浏览器核验重点筛选、
来源分歧、事件详情、股票与板块联动；公网401/200及只读路径限制正常。
旧版新闻按日预算逐步升级，页面会保留待升级状态。审阅和验收摘要见
`news-v3-acceptance-20261102.json`。
