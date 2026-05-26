# BtrFS Cleaner

BtrFS 文件系统管理面板 —— 飞牛 OS (fnOS) 第三方应用。

![version](https://img.shields.io/badge/version-1.2.1-blue)
![platform](https://img.shields.io/badge/platform-fnOS%200.8%2B-brightgreen)

---

## 功能

- **Scrub 调度** — 支持每周/每月定期 Scrub，自动执行
- **实时进度查看** — 通过 Web UI 查看 Scrub 执行进度
- **文件系统状态监控** — 一键查看 Btrfs 文件系统健康状况
- **CGI 代理架构** — 通过 bash CGI 代理转发请求到本地 Flask，安全性好
- **PAM 认证集成** — 复用系统用户认证
- **零依赖启动** — 自动检测 Python 版本，自动安装缺失的 pip 包

## 架构

```
用户请求 → fnOS App Center/浏览器
      ↓
nginx (trim_http_cgi)
      ↓
proxy.cgi (bash CGI 代理)
      ↓
Flask app.py (localhost:5100)
      ↓
sudo btrfs 命令
```

- `proxy.cgi` 将 HTTP 请求从 nginx 转发到本地的 Flask 服务
- `app.py` 仅接受 `127.0.0.1` 的连接，外部无法直接访问
- 所有 Btrfs 操作通过 `sudo btrfs` 执行

## 安装

### 方式一：通过 fnOS App Center

1. 下载 [btrfs-cleaner.fpk](btrfs-cleaner.fpk)
2. 在 fnOS App Center 中手动安装

### 方式二：源码部署

```bash
git clone https://github.com/dalingo81/btrfs-cleaner.git
cd btrfs-cleaner

# 安装依赖
pip install flask apscheduler requests

# 启动服务
bash cmd/main start    # 或 exec /start.sh
```

## 配置

配置文件路径：`${TRIM_PKGETC}/config.env`

```env
BTRFS_CLEANER_PORT=5100
BTRFS_CLEANER_HOST=0.0.0.0
BTRFS_CLEANER_LOG_DIR=/tmp/btrfs-cleaner/log
```

## 项目结构

```
btrfs-cleaner/
├── app/
│   ├── server/
│   │   └── app.py           # Flask 后端 API
│   ├── ui/
│   │   ├── proxy.cgi        # CGI 代理脚本
│   │   ├── config           # 桌面 UI 配置
│   │   └── images/          # 应用图标
│   └── www/
│       ├── index.html       # 主界面
│       └── login.html       # 登录页面
├── cmd/
│   ├── main                 # 服务启停脚本
│   ├── install_callback     # 安装回调
│   ├── install_init         # 初始化脚本
│   ├── uninstall_callback   # 卸载回调
│   ├── uninstall_init       # 卸载清理
│   ├── upgrade_callback     # 升级回调
│   ├── upgrade_init         # 升级准备
│   ├── config_callback      # 配置保存回调
│   └── config_init          # 配置初始化
├── config/
│   ├── privilege            # 权限配置
│   └── resource             # 资源定义
├── manifest                 # FPK 应用清单
├── ICON.PNG                 # 应用图标（64px）
├── ICON_256.PNG             # 应用图标（256px）
└── .gitignore
```

## 技术栈

- **后端：** Python 3 + Flask
- **前端：** HTML + JavaScript (原生)
- **代理：** Bash CGI
- **调度：** APScheduler
- **系统：** fnOS 0.8+ (Debian 12)

## 许可证

MIT License

---

*Made by [dalingo](https://github.com/dalingo81) — 飞牛论坛攻略参考：https://club.fnnas.com/forum.php?mod=viewthread&tid=59220*
