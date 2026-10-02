# NetMon 网络波动监控

轻量级局域网/公网连通性与延迟监控工具：Python 后端定时 Ping 各目标，检测断线与延迟波动，写入 CSV 存档，并通过本地 Web 面板（http://127.0.0.1:8787）实时展示。

## 功能

- 多目标监控：局域网网关、公网 DNS、任意 IP/域名（配置热加载，改 config.txt 即生效）
- 波动检测：连续超时判断线，延迟超过近期均值阈值判波动，自动记录事件
- 实时仪表盘：卡片概览、15 分钟/1 小时/当天延迟曲线（Canvas 绘制）、事件记录
- 历史回看：延迟曲线与事件记录均支持按日期选择（data/YYYY-MM-DD.csv + events.log）
- 数据持久化：CSV 存档 + 事件日志，重启后自动恢复历史曲线

## 运行环境

- Windows（依赖系统 ping 命令），Python 3.11+（开发于 3.12）
- 仅使用 Python 标准库，无第三方依赖

## 快速开始

1. 编辑 `config.txt` 配置监控目标（每行：名称,IP或域名,类型）
2. 双击 `启动网络监控.bat`（自动查找 Python 并后台启动，随后打开面板）
3. 双击 `停止监控.bat` 停止监控

或手动运行：

```
python monitor.py
```

启动后访问 http://127.0.0.1:8787

## 配置说明

`config.txt` 每行一个目标：`名称,IP或域名,类型`

- `local`：局域网 / 网关
- `internet`：公网目标
- `custom`：自定义目标

修改保存后自动热重载，无需重启。

## 数据文件（data/ 目录，不入库）

- `YYYY-MM-DD.csv`：每日采样数据（时间,目标,延迟ms,状态）
- `events.log`：波动事件记录
- `run.log`：运行日志

## API

- `/api/now`：当前各目标状态
- `/api/history?name=X&minutes=N`：近 N 分钟延迟点
- `/api/history?name=X&date=YYYY-MM-DD`：某天全天延迟点
- `/api/events`：最近事件（实时）
- `/api/events?date=YYYY-MM-DD`：某天事件记录
- `/api/targets`：目标列表

## 免责声明

示例配置中的 `203.0.113.x` 为 RFC 5737 文档保留地址，实际监控目标请自行修改。
