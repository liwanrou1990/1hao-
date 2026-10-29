# 手机（iPhone）安全访问方案

原则：Streamlit 只监听 127.0.0.1，**不**直接暴露到公网、不开无认证端口。远程访问走私有网络。

## 方案 A（推荐）：Tailscale Serve（tailnet 内 HTTPS，零公网暴露）
1. 在运行 Dashboard 的 Mac/服务器安装 Tailscale 并登录：`brew install --cask tailscale`（Mac）或 `curl -fsSL https://tailscale.com/install.sh | sh`（Linux）。
2. 在同一 Tailscale 账号下给 iPhone 安装 Tailscale App 并登录。
3. 主机上执行：`tailscale serve --bg --https=443 http://127.0.0.1:8501`
   - 得到 `https://<主机名>.<tailnet>.ts.net/`；证书由 Tailscale 自动签发；只有你的 tailnet 设备能访问。
   - `tailscale serve status` 查看；`tailscale serve reset` 关闭。
4. iPhone Safari 打开 `https://<主机名>.<tailnet>.ts.net/?m=1`（手机精简视图），“添加到主屏幕”。
5. 健康检查：`curl -s http://127.0.0.1:8501/_stcore/health` → `ok`。

## 方案 B：SSH 隧道（已有 SSH 密钥访问时）
手机端用支持端口转发的 SSH 客户端（如 Termius/Blink）：`ssh -N -L 8501:127.0.0.1:8501 user@host`，然后打开 `http://127.0.0.1:8501/?m=1`。

## 方案 C（不推荐，仅当 A/B 不可用）：反向代理 + 认证 + HTTPS
Caddy 示例（需域名）：
```
dash.example.com {
  basicauth { user <caddy hash> }
  reverse_proxy 127.0.0.1:8501
}
```
仍建议配合防火墙只放行固定 IP。**绝不**把 `--server.address 0.0.0.0` 的裸 Streamlit 暴露到公网。

## 安全清单
- 密钥/Webhook 只放 `~/.config/spot90/env`（chmod 600），日志与页面从不打印（每日脚本只记录 “webhook configured: yes/no”）。
- 系统只读：代码中无任何下单/提现接口（健康检查第 6 项自动扫描）。
- Dashboard 只展示快照文件，网页进程不访问任何交易所私有接口。
