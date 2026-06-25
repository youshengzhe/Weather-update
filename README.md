# 天气通知

这套脚本支持两种运行方式：

- 本地 Windows 计划任务
- GitHub Actions 云端定时运行

## 当前规则

- 固定地点：宝丰县、武侯区
- 每天 22:00 推送两个地点“接下来一天”的天气
- 有雨或雪时，尽量写出预计开始时间
- 其余时间每小时整点检查未来 3 小时
- 只有预计有雨雪的地点才推送
- 22:00 到次日 08:00 之间整点静默
- 如果当前已经在下雨或下雪且后续持续，不重复推送

## 本地运行

本地保留 `config.json` 即可：

```powershell
python app.py tomorrow
python app.py hourly
```

## GitHub Actions 运行

把仓库推到 GitHub 后，在仓库的 `Settings -> Secrets and variables -> Actions` 里添加这些 Secrets：

- `QWEATHER_API_HOST`
- `QWEATHER_KID`
- `QWEATHER_PROJECT_ID`
- `QWEATHER_PRIVATE_KEY_PEM`
- `WXPUSHER_SPT`

工作流文件在：

- `.github/workflows/weather-notify.yml`

调度时间已经按中国时区换算好了：

- `14:00 UTC` = `22:00 Asia/Shanghai`，执行 `tomorrow`
- `00:00-13:00 UTC` 每小时一次，对应中国时间 `08:00-21:00`，执行 `hourly`

现在工作流会直接按触发它的 cron 来选模式，不再依赖“实际执行时的 UTC 小时”，这样 GitHub 即使延迟几分钟或一小时，也不会把 22:00 的那次错分成 `hourly`。

你也可以在 GitHub Actions 页面手动运行，并选择：

- `tomorrow`
- `hourly`
- `test-wechat`

## 安全说明

- `config.json` 已加入 `.gitignore`，避免把本地密钥直接提交
- 仓库里新增了 `config.example.json`，只保留结构和地点，不含敏感信息
