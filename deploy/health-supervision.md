# 执行期限与进程监督

应用的 `RuntimeLimiter` 在所有工作流入口、scheduler 和隐私管理操作登记真实执行，不随浏览器断开而注销。软期限默认120秒；同步线程未退出时，应用 watchdog 在期限后15秒以非零状态退出，SQLite 由已有启动恢复处理。

容器内每秒原子写入 `/tmp/runtime-status.json`，只包含单调时间、活跃 deadline 和 ready。独立 healthcheck 进程检查该文件；文件超限/非法、心跳超过5秒、执行超过宽限均失败。Compose 设置2秒检查、1秒单次超时、启动宽限60秒。单独的 unhealthy 状态不会自动触发 Compose 重启。

宿主机必须持续运行：

```sh
python scripts/supervise-runtime.py --project qingji-hardened
```

它只查找并反复核验固定项目标签及 app 服务身份，回收不健康/异常退出的应用容器；不接受任意容器 ID，不访问健康数据库，不读取秘密。Docker 命令本身有15秒上限；正常控制面响应下，检查与终止余量目标为15秒。如果 Docker/主机失去响应，属于平台故障，不能声称应用已在150秒内终止。

10分钟内超过3次监督重启时停止应用并退出，避免无限自动恢复。宿主机服务管理器不能无条件无限重启已因该保护退出的监督器。用 systemd（Linux）或受管后台计划任务（Windows）托管，记录监督器退出并请求运维处理；应用容器不挂载 Docker socket。`start-hardened.ps1` 前台运行监督器，适合检查部署，持续使用需宿主机托管。

回收会中断同进程其他请求和 scheduler。这是当前单用户单进程方案的边界，不是单任务强制终止。重启后保留原 request_id/调用预算，按已有 checkpoint、resume_attempt、receipt 与删除 fence 恢复。不可自动新建幂等键或假定整轮回滚。

必须以测试卷执行故障注入，验证已提交/未提交记录、两库、删除与 resume 恢复。禁止在真实个人数据卷上执行故障注入。
