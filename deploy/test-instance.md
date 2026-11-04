# 测试实例（chenjuyin 分支）

- 服务器：49.234.12.178，地址 https://49.234.12.178:8789（Basic Auth 与正式站相同）
- 代码目录：/opt/sector-dashboard-test（releases/<sha> + current 软链）
- 服务：sector-dashboard-test.service（uvicorn 127.0.0.1:8790，nginx 8789 反代）
- 数据：/var/lib/sector-dashboard-test/market.sqlite3（演示数据，与正式库完全隔离）
- 自动更新：sector-dashboard-test-update.timer 每分钟轮询 chenjuyin 最新提交，
  发现新提交后自动下载、构建前端、切换软链并重启；日志见 /var/log/sector-dashboard-test-update.log
- 构建失败会保留旧版本运行，不会把坏代码切上去
