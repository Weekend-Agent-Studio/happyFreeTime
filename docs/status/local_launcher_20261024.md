# 本地双模式启动器

_Current，2026-10-24，沿用公网 Demo 开发分支。_

## 实现

- `scripts/start_local.ps1` 提供一个 PowerShell 入口；`scripts/start_local.py` 以子进程独立环境启动本项目 Python/Uvicorn 与已安装的 Vite，统一检查就绪并清理自己启动的进程。
- 默认 PublicDemo：8001/5174，独立 local_public_demo.db，访客身份/临时 Key/预置案例，与 Docker 预设一样关闭部署者 LLM Key 和付费站点服务。
- Development：8000/5173，既有开发数据库 happy_free_time_v2.db、固定开发身份；默认规则模型。显式 -FullLlm 使用 .env 中模型配置与 hybrid 检索，站点 Provider 沿用当前进程/.env，不自动启用地图服务。
- 参数自动同步前后端开关和代理；父进程环境与 .env 不改写。端口占用先报错，不杀已有服务；支持自定义端口。日志放入被忽略的 .tmp/local-run/，子服务不弹新窗口。
- 前端 HMR 自动更新；后端未加 reload，多进程/重载均不启用。Ctrl+C 或任一服务退出时停止本次两个子进程。-Check 仅启动、检查模式/HTML 并退出，不打开浏览器、不发规划或模型调用。
- 不增加前台按钮或公共后端模式切换 API；正式镜像仍固定公网模式，启动器不进入构建上下文。

## 验证

- 定向测试 6 passed：父环境不变、两个模式开关/数据库、默认离线与显式 LLM、拒绝公网 FullLlm、端口占用保护与非法端口。
- PowerShell 实际 -Check：PublicDemo 默认 8001/5174 通过；Development 使用自定义 18111/15173 通过。最终日志重定向后的 PublicDemo 再次检查通过；退出后复查相关端口可绑定。
- 未运行真实 Key、Docker 或公网部署；前端与领域代码未改，不重复其整套回归。
