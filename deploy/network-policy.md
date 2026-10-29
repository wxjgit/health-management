# 运行网络策略

本配置使用四个固定且独立的 `/28` 子网。部署前确认 `172.30.80.0/28`、`172.30.81.0/28`、`172.30.82.0/28`、`172.30.83.0/28` 不与主机/VPN/其他 Docker 网络冲突。应用的两个地址为 `.80.3`、`.81.3`，出口代理在 `.81.2:3128`。`.83.0/28` 仅连接发布回环端口的网关，应用不加入。如果修改地址，必须同时修改 Compose、NGINX、Squid、网络策略和探测。

应用不加入外网网络，禁用 IPv6，外部 DNS 指向无监听的自身回环；通过静态容器地址解析 egress，避免向 Docker 外部解析器发送任意域名。它仍需要宿主机层阻断网桥网关和其他本机服务；`internal: true` 不能替代此项。

## Linux Docker Engine（iptables 后端）

启动前阅读 `scripts/apply-network-policy.py`。由有权限的管理员显式执行：

```sh
sudo python3 scripts/apply-network-policy.py --apply
```

脚本只新增 `QINGJI-HARDENED` 链及针对两个应用地址的规则，保留 Docker 和其他服务的规则；遇到已有同名但内容不同的链即失败，不清空全局防火墙。它允许已建立连接和唯一代理目的端口，其他应用发起的转发全部丢弃；另在 INPUT 阻断应用访问宿主机。规则需由宿主机管理方式持久化，Docker/主机重启后再次检查。

使用 nftables 后端且无 DOCKER-USER、rootless Docker 或子网已冲突时，该脚本拒绝操作。应由部署环境提供等效规则并重新进行正/负向探测，不能为了执行脚本切换宿主机全局防火墙后端。

撤销时先停止本项目，删除 INPUT 与 DOCKER-USER 中仅匹配上述两个源地址的新增规则，再删除本项目专用链。先核对规则内容和归属，不执行 `iptables -F`、不修改其他项目的规则。

## Windows Docker Desktop

Compose 的非 root、只读、配额与内部网络可以在 Linux VM 内运行。Windows 防火墙规则不能直接等同于 VM 内网桥/host.docker.internal 的访问限制，Linux 脚本也不能直接在 Windows 主机上执行。

当前交付没有自动修改 Docker Desktop VM 防火墙。此平台只能记录已验证的容器级控制，宿主机隔离必须保持“未验收”，直到配置并验证等价的受管策略。不要给应用挂 Docker socket、使用 privileged 或关闭 TLS 来消除探测失败。

## 验证要求

运行 `scripts/verify-runtime.py` 获得基础探测报告。端口超时本身不足以证明网络隔离：还要提供受控公网服务的可达正向对照、通过代理的成功连接、非白名单/私网/元数据/IPv6拒绝、DNS 换址测试与宿主机监听端口的拒绝结果。

探测只允许使用合成数据和假密钥；不发送真实健康记录。基础脚本不会因未验证的项目自动写出 `accepted=true`。

微信登录是必需能力。出口代理精确允许模型供应商 `dashscope.aliyuncs.com` 和微信登录服务 `api.weixin.qq.com` 的 HTTPS 连接；保持私网、元数据地址和其他域名拒绝策略。部署验收需分别验证模型调用与微信登录。
