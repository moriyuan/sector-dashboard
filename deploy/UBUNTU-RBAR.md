# Ubuntu-RBAR 部署

2026-11-03 部署至腾讯云轻量应用服务器 Ubuntu-RBAR，上海地域，实例
`lhins-f2jnvbeo`，访问地址 https://49.234.12.178/。云端为 Ubuntu 26.04、
Python 3.14，2 核 1 GB 内存，已有 2 GB swap。应用在 loopback 8788，
Nginx 在 443 提供 HTTPS 和独立 Basic Auth，只代理已列举的只读 API。
服务器无需 Node.js。

- 当前发布目录为 `/opt/sector-dashboard/releases/20261103-financing-v1`，旧版本 `20261103-midday-v1`、`20261103-rbar-v1` 保留，`current` 软链指向新版本。
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

2026-11-03 增加交易日北京时间 12:00 午间行情归档，保留 15:30 收盘归档和
16:30 复核。`midday` 与 `close` 的行情、板块、资金、涨幅榜及修订分别读取；
不使用下午行情补造已错过的午间数据。本机仍禁用采集。首个新午间窗口为
11月4日，实际来源覆盖待当次采集核验。

升级前一致性备份为 `/var/backups/sector-dashboard/pre-20261103-midday-v1.sqlite3`，
通过 SQLite quick_check。发布清单、HTTPS 验收及截图在本机
`runtime/rbar/midday-20261103/`。141 项自动化测试、生产构建及云端只读 API
验收通过，包含午间空档隔离、既有390概念、个股榜、新闻、74条龙虎榜披露和
10只ETF归档。低权限服务账号已验证代码可读；数据库及网关配置未变更。

回滚仅需将 `current` 恢复为 `20261103-rbar-v1` 并重启 `sector-dashboard`。
本次新闻代码未变更，原新闻进程保持运行，后续重启会使用 current 版本；
不要用升级前备份覆盖之后采集的数据。

2026-11-03 增加市场总览的沪深融资余额周线。云端实测两市各503个交易日，
最新业务日期11月2日，合计22,951.07652994亿元；与沪深交易所最新披露核对。
融资源通常下一交易日披露，独立线程在交易日09:15、12:00、16:30检查，
源时间未知、原始响应及修订均保留；周末缺失和事后回补有单独标记。

该次一致性备份在 `/var/backups/sector-dashboard/pre-20261103-financing-v1.sqlite3`，
quick_check通过，发布清单与验收在本机 `runtime/rbar/financing-20261103/`。
149项测试通过；云端 HTTPS、融资时段隔离与旧功能只读接口通过检查。
仅重启网页/行情服务，新闻服务和网关配置不变；无需新增公网API路径。
回滚将 current 恢复为 `20261103-midday-v1` 并重启 sector-dashboard，保留数据库。


2026-11-03 自动更新修复：前端每60秒及恢复可见时读取缓存，默认跟随最新归档；
手选日期/时段后固定，日历快捷项和“跟随最新”可恢复同日收盘。背景请求失败
保留已有值，详情固定打开时的修订。本机明确显示只读存档，不自动同步云端。
静态文件热更新未重启采集，入口备份为
`/var/backups/sector-dashboard/index.pre-20261103-auto-update.html`，旧静态资源保留。

同次排查确认15:30任务真实执行，指数、市场、情绪、行业及个股榜已生成11月3日归档。
同花顺目录发生跨页重复及部分链接空CID，现统一按名称排序；空CID只接受
官方导航唯一身份或经官方当日指数响应重新核验的唯一候选，其他异常继续拒绝。
当日目录390个；报价源返回502的缺口继续标注并按退避重试。成分核验仍在补齐，热点评分覆盖以页面为准，不用前一日成员替代当日核验。
源码备份为 `/var/backups/sector-dashboard/ths.pre-20261103-stable-directory.py`，
身份核验修订前备份为 `ths.pre-20261103-official-identity.py`（同目录）。
153项Python测试、6项Node选择边界测试和生产构建通过；浏览器验证了实际轮询、
固定午间和恢复同日最新收盘。午间功能首次上线晚于当日窗口，11月3日午间缺口
保留，首个可自动执行的午间窗口仍为11月4日12:00。

2026-11-03 龙虎榜列表增加「当日涨跌幅」，按所选日收盘快照匹配，三日累计榜
同样展示单日涨跌幅，缺失保留为空。16项龙虎榜／新闻联动测试、生产构建及
本地和云端浏览器检查通过；HTTPS实测11月3日81条、11月2日74条披露均有报价。
仅更新 `backend/lhb_api.py` 与前端资源并重启网页服务；采集开启、新闻服务和
网关正常，本机仍为只读存档。回滚恢复
`/var/backups/sector-dashboard/lhb_api.pre-20261103-lhb-change.py` 和
`/var/backups/sector-dashboard/index.pre-20261103-lhb-change.html` 到原路径并重启
`sector-dashboard`；数据库和旧静态资源保留。校验包、清单和截图位于本机
`runtime/rbar/lhb-change-20261103/`。

同日追加三日累计列：三日榜并列展示当日涨跌幅与截至榜单日的三个交易日复合涨跌幅，
显示窗口，缺任一日收盘价或昨收参考价即留空；输入行情修订可追溯，asof统一北京时间。
17项相关测试、生产构建和本地双列显示检查通过，云端接口校验通过。
本轮回滚文件为同目录的 `lhb_api.pre-20261103-lhb-three.py` 与
`index.pre-20261103-lhb-three.html`；最后时区修正另保留 `lhb-three-tz` 版本备份。
本机校验截图、部署包和脚本在 `runtime/rbar/lhb-three-20261103/`。

2026-11-03 个股涨幅榜增加「次日连板概率」及样本、区间、独立审计依据弹层。
模型 `relay-group-v1` 使用截至11月2日的历史分组统计，按涨跌停制度及连板高度
分组，非个股专属校准；仅收盘封板、口径核验及样本门槛满足时展示，其他情况
保留空值及原因。历史日期不套用未来模型，11月2日明确标记事后估算；模型有效期
至12月2日，未新增自动训练。30项相关测试通过，浮点整数连板高度兼容修正后
4项专项测试再次通过，生产构建、本地弹层和云端HTTPS检查通过。

云端11月3日榜单48只股票获得估计，10月30日未泄漏当前模型；网页、新闻及网关
服务正常，云端采集继续运行，本机保持只读。仅更新网页服务代码及前端资源，
无数据库迁移。回滚恢复 `/var/backups/sector-dashboard/app.pre-20261103-relay.py`
与 `index.pre-20261103-relay.html` 到当前版本对应路径并重启 `sector-dashboard`；
保留数据库及旧静态资源。部署清单、校验包与截图在 `runtime/rbar/relay-20261103/`。

2026-11-06 每日汇总上线：发布目录 `/opt/sector-dashboard/releases/20261106-daily-v1`，
从当前线上版本复制后仅覆盖每日汇总相关后端与前端构建，保留其他线上修订及旧静态资源。
新增交易所风险监控、公开盘口异动、当日财报及行业对照研究；新闻 worker 独立线程采集，
模型分析继续与新闻共享北京时间每日一美元预算。本地仍只读，未上传或覆盖本地数据库。

一致性数据库备份、原网关和发布记录位于
`/var/backups/sector-dashboard/daily-20261106/`。首次网关检查因正则量词语法失败自动回滚；
修正后 `nginx -t` 通过，已切换新版本。新增 `/api/daily` 与报告详情只读路径，
保持 HTTPS 与原 Basic Auth，未新增端口。50 项专项测试与生产构建通过；公网实测
未认证 401、POST 405、非法路径 404、新汇总与原行情／涨幅榜／龙虎榜／新闻接口 200。
网页和新闻服务均 active、NRestarts=0。验收和补丁清单保存在本机
`runtime/rbar/daily-20261106/`（忽略目录，不含新增凭据）。

回滚：恢复 `current` 为 `20261103-financing-v1`，恢复上述备份目录的 `nginx.conf`
到 `/etc/nginx/sites-available/sector-dashboard`，运行 `nginx -t`，重启
`sector-dashboard sector-news-worker` 并 reload Nginx。增量表可保留，不用旧数据库覆盖
上线后的记录。新依赖 `pypdf>=5,<7` 兼容旧版本，回滚无需移除。

首轮云端采集已读取上交所、深交所与公司公告，自动补采11月5日华虹宏力财报并生成研究记录。
华西证券接口在上海返回 TLS legacy renegotiation 不兼容，北交所仍有覆盖缺口；
保留错误状态，未降低 TLS 安全设置。当前公网验收通过，内嵌浏览器受 Basic Auth
登录拦截，本轮未重复云端浏览器交互验收（本地页面此前已验证）。

2026-11-06，上海发布 `20261106-barra-v1`。从线上 `20261106-daily-v1` 复制发布目录，
新增只读 `/api/barra`、因子收益页面和收盘估值字段，沿用原网关认证。
数据库一致性备份和原网关位于 `/var/backups/sector-dashboard/barra-20261106/`。
回滚时将 current 指回 `20261106-daily-v1`，恢复备份中的 nginx.conf 至站点配置，
检查 nginx -t、重启 sector-dashboard 并 reload Nginx。新闻 worker 独立运行。
公网实测 11 月 5 日回归覆盖 5,011 / 5,026 只、31 个行业，前一日查询隔离、
构建文件散列、认证和只读权限通过。验收记录在 `runtime/rbar/barra-20261106/acceptance.json`。

2026-11-06，上海发布 `20261106-ths-detail-v1`。概念详情按所选快照的截止时间读取同花顺当日、同时段已归档的目录领涨股及成分页，区分完整名单与可核对样本。补采轮转并保留失败退避。公网核对 11 月 5 日 BC 电池收盘、HJT 电池午间、TOPCON 电池收盘均返回目录领涨股及 10 只样本。全部分页的后续覆盖仍受来源可用性影响。回滚将 current 指回 `20261106-barra-v1` 并重启 sector-dashboard。本次沿用现有数据库及网关。

2026-11-06 上海发布 `20261106-quant-v2`，新增 `/api/barra?model=price_volume`，
四因子归档使用 `model=archive`。沪深 A 股历史结果覆盖 2024-11-06 至 2026-11-05
全部 485 个交易日。独立数据目录 `/var/lib/sector-dashboard/quant-price-v2` 含
252 日预热输入、历史日线/换手/市值/行业/基准、按日回归结果及 `audit.json`。
区间 5,277 只、2,507,105 行，两种市值同时有效 2,500,990 行。逐日核验输入指纹和有效行业版本，
最近日与独立回归的八个风格系数最大差异低于 1e-14 个百分点。
191 项测试通过；公网实际校验三个日期的完整结果、旧模型、前端文件、认证和只读限制。

`sector-quant-update.timer` 北京时间 20:40、23:10、08:10 启动独立更新服务，
更新下载缓存在 `/var/lib/sector-dashboard/quant-price-sources`，专用依赖目录为
`/opt/sector-dashboard/quant-worker-libs`。本地镜像保持只读使用方式。
回滚时停用定时器及更新服务，将 current 切回 `20261106-ths-detail-v1` 并重启
sector-dashboard。新版数据目录独立保留。发布清单和 HTTPS 验收脚本在忽略目录
`runtime/rbar/quant-v2-20261106/`。

2026-11-06 上海上线 `20261106-resonance-v1`，页面 `#resonance`，网关新增已认证只读
`/api/resonance`。独立目录 `/var/lib/sector-dashboard/resonance/days` 收录 11 月 2–6 日：
两个宽基共 75 段转折、1,048 个板块波段匹配。匹配数按日期、基准、波段和板块计。
已从原始分钟独立核对启动、同分钟并列、回踩守住案例；194 项测试通过。公网核验包含
真实静态资源、日期隔离、股票切换、旧八因子接口、未认证 401 和写方法 405。
`sector-resonance.timer` 北京时间 18:10、22:15 检查当日收盘，已归档日期跳过。
回滚时停用该定时器和服务，恢复 `current` 至 `20261106-quant-v2`，
将 `/var/backups/sector-dashboard/resonance-20261106/nginx.conf` 恢复至原站点配置，
检查 nginx -t、重启 sector-dashboard 并 reload Nginx。保留独立分钟归档。

2026-11-06 共振基准更新为上证指数、创业板指、科创综指，版本
`20261106-resonance-three`。原始股票分钟及当日成分分区未改写；
`scripts/resonance_reindex.py` 从冻结分区补算 11 月 2–6 日的三个指数。
仅增量加入各指数结果，同一网关与定时器继续使用。若需回滚本次发布，
切回 `20261106-resonance-v1` 并重启 sector-dashboard；原五日的旧指数文件保留。

2026-11-08，上海发布 `20261108-lhb-table`，龙虎榜顶部增加游资参与股票汇总。
单股一行、单日与三日分列、重复披露去重、金额冲突不累计；点击进入原有席位证据详情。
上一发布 `20261108-lhb-expanded` 保留，数据库备份为
`/var/backups/sector-dashboard/pre-20261108-lhb-table.sqlite3`（quick_check 通过）。
无数据库迁移及网关变更，仅重启 sector-dashboard；新闻服务保持运行。
回滚将 current 指回上一发布并重启 sector-dashboard，勿覆盖之后采集的数据。
该发布通过15项龙虎榜测试与生产构建；实际浏览器核验23只股票、13组关联席位、
分页和详情来源展开，未登录401、登录200，网页/新闻/nginx均active，采集开启。

2026-11-09，上海发布 `20261109-seat-backtest`，增加 `#seat-backtest` 游资回测页，
支持月度及1/3/5/10日持有期、席位排名、未入场原因、逐笔详情和报告下载。
只读研究包 `research/seat-follow-v1` 约19MB，冻结样本截至2026-11-05，
不自动重算。22项相关测试、生产构建、公网鉴权/只读接口与桌面/手机页面验收通过。

同日依用户要求，对**上海主站及测试站全部模块做一次两日历史清理**；本地存档不动。
行情、龙虎榜、因子输入/结果和分钟分区保留2026-11-05、2026-11-06及当前新采集记录；
新闻按清理时最近48小时保留，下限为2026-11-07T02:51:13.072634Z。
财报及异动等按披露/交易日期清理，并清理关联分析、旧响应缓存、旧数据库备份及数据中转包。
保留交易日历、业务映射、用户待办、当前预算/服务配置及完整游资回测研究包。
旧文中提及的历史数据库备份现已按本次授权删除，不能再用于回滚历史数据。
主库从5,651,632,128缩减到368,128,000字节，测试库从7,298,068,480缩减到
1,725,820,928字节；临时旧库经完整性、日期边界及接口验收后删除。
服务器可用空间恢复约27GB，磁盘使用率29%。主站/测试站、新闻worker、网关与原定时器已恢复。
市场、板块详情、个股、龙虎榜、新闻、每日汇总、因子及共振接口均返回200，
真实浏览器复核回测、龙虎榜、新闻页面无运行错误。

防止RSS重放已清理报道：两库增加 `cleanup_news_floor_insert`、
`cleanup_news_floor_update` 固定新闻日期下限触发器，以及
`cleanup_news_raw_cache` 原始新闻响应不落库触发器；解析后的新报道及分析正常保存。
若以后明确要求新闻历史回填，应先审阅并删除这些触发器。
本次是存量清理，**未新增全平台滚动两日清理定时器**；后续采集继续积累。
依赖长历史的曲线/指标会缺失，因子继续计算需重新积累足够输入。
操作脚本与页面验收截图在本机忽略目录 `runtime/cleanup-20261109/`，
两库旁保存 `cleanup-20261109.json`，文件清理清单在主站 `file-cleanup-20261109.json`。
