# fnOS Fan WebUI — Docker 安装部署与使用说明

> 本文档为项目 README 的可分发副本。最新版请访问：<https://github.com/wwu-unique/fnos-fan-webui>

---

## 项目与镜像

- **源码仓库**：<https://github.com/wwu-unique/fnos-fan-webui>
- **Docker 镜像**：`ghcr.io/wwu-unique/fnos-fan-webui:1.0.0`
- **WebUI 地址**：`http://NAS_IP:8080/`

## 适用范围

| 环境 | 结论 |
|---|---|
| 飞牛 fnOS / flymNAS，**QU-605** | ✅ 已在真实硬件验证：`qnap8528`、PWM 写入、RPM、自动曲线及 WebUI 均正常。 |
| 其它 x86 NAS / Linux 主机 | ⚠️ 仅在已存在 `qnap8528`、`pwm1` 与 `fan1_input` 时尝试。 |
| 威联通 QNAP QTS / QuTS hero 成品 NAS | ❌ 未验证，不建议直接部署。 |
| Windows、macOS、Docker Desktop、无真实 PWM 的虚拟机 | ❌ 不支持。 |

> **重要风险**：本项目需要 `privileged: true`，并以读写方式挂载宿主机 `/sys/class/hwmon`，用于写入真实风扇 PWM。请仅在可信局域网使用，首次调试务必有人值守。

---

# 一、Docker 安装部署

## 1. 检查 Docker Compose

在 fnOS 的 SSH 终端执行：

```bash
docker --version
docker compose version
```

需要 Docker 和 Docker Compose v2 均可用。

## 2. 检查硬件兼容性

**若这一步不通过，请停止部署。容器不能替代宿主机内核模块。**

```bash
for h in /sys/class/hwmon/hwmon*; do
  [ -r "$h/name" ] || continue
  if [ "$(cat "$h/name")" = "qnap8528" ]; then
    echo "FOUND: $h"
    ls -l "$h/pwm1" "$h/fan1_input"
  fi
done
```

成功时应看到类似：

```text
FOUND: /sys/class/hwmon/hwmon4
.../pwm1
.../fan1_input
```

继续只读检查：

```bash
HWMON=$(for h in /sys/class/hwmon/hwmon*; do
  [ -r "$h/name" ] && [ "$(cat "$h/name")" = qnap8528 ] && echo "$h" && break
done)

cat "$HWMON/pwm1"
cat "$HWMON/fan1_input"
```

- `pwm1` 应返回 `0–255` 的数值；
- `fan1_input` 应返回非零风扇转速原始值；
- 任意节点缺失：先处理宿主机 `qnap8528` 内核模块与驱动问题。

## 3. 下载 Compose 配置

```bash
sudo mkdir -p /opt/fnos-fan-webui/data
cd /opt/fnos-fan-webui

sudo curl -fsSLo docker-compose.yml \
  https://raw.githubusercontent.com/wwu-unique/fnos-fan-webui/main/docker-compose.yml

grep 'image:' docker-compose.yml
```

预期镜像：

```text
image: ghcr.io/wwu-unique/fnos-fan-webui:1.0.0
```

该版本已固定，首次安装不会因 `latest` 自动升级到未知版本。

## 4. 启动容器

```bash
cd /opt/fnos-fan-webui
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
```

正常示例：

```text
fnos-fan-webui   Up   0.0.0.0:8080->8080/tcp
```

## 5. 验收与打开页面

NAS 本机执行：

```bash
curl -fsS http://127.0.0.1:8080/api/status
```

返回 JSON，且包含温度、PWM、RPM 与 `system` 字段，即服务运行正常。

浏览器访问：

```text
http://NAS_IP:8080/
```

例如：

```text
http://10.10.10.200:8080/
```

---

# 二、首次使用

## 1. 先确认实时数据

进入 WebUI 后，先确认以下信息有合理数据：

- 当前控制温度；
- CPU / 主板 / NVMe 温度；
- 风扇 RPM；
- 当前 PWM 输出；
- 高级诊断：`模块：已加载`、`PWM：可读写`。

数据不正常时，不要先修改曲线，请跳到“故障排除”。

## 2. 自动曲线（推荐日常使用）

默认模式为自动。服务根据温度与曲线自动计算 PWM，并包含移动平均、迟滞、输出平滑，避免频繁升降速。

操作流程：

1. 保持「自动」模式；
2. 修改各温度阈值对应的 PWM；
3. 保持曲线平滑上升：后一个温度点的 PWM 不应低于前一个；
4. 点击「确认应用」；
5. 观察温度、PWM 与 RPM 至少 10 分钟。

### 默认曲线

| 温度上限 | PWM 原始值 | 约占比 |
|---:|---:|---:|
| 35°C | 50 | 20% |
| 40°C | 65 | 25% |
| 45°C | 80 | 31% |
| 50°C | 100 | 39% |
| 55°C | 120 | 47% |
| 60°C | 150 | 59% |
| 65°C | 180 | 71% |
| 70°C | 210 | 82% |
| 75°C | 235 | 92% |
| 80°C | 255 | 100% |

> 默认曲线是 QU-605 的实测起点，并非其它设备的通用安全阈值。

## 3. 手动模式（仅短时测试）

手动模式会暂停自动温控，只用于确认 PWM 是否能驱动 RPM 变化：

1. 记下当前温度、PWM、RPM；
2. 切换到「手动」；
3. 从 40%–50% 的中间输出开始；
4. 应用后观察 RPM 是否变化；
5. 测试完毕，**立即切回自动模式并确认应用**。

不要无人值守地长时间保持手动低 PWM。

---

# 三、可选：硬盘 / SSD 温度采集

该项用于让 SATA / SMART 磁盘温度参与风扇保护策略。

## 前提

```bash
smartctl --version
```

若没有 `smartctl`，请先按 fnOS 实际软件源安装 `smartmontools`。

## 安装并启用采集器

```bash
sudo curl -fsSLo /usr/local/sbin/collect_disk_temps.sh \
  https://raw.githubusercontent.com/wwu-unique/fnos-fan-webui/main/collect_disk_temps.sh
sudo chmod 0755 /usr/local/sbin/collect_disk_temps.sh

sudo curl -fsSLo /etc/systemd/system/collect_disk_temps.service \
  https://raw.githubusercontent.com/wwu-unique/fnos-fan-webui/main/systemd/collect_disk_temps.service
sudo curl -fsSLo /etc/systemd/system/collect_disk_temps.timer \
  https://raw.githubusercontent.com/wwu-unique/fnos-fan-webui/main/systemd/collect_disk_temps.timer

sudo systemctl daemon-reload
sudo systemctl enable --now collect_disk_temps.timer
```

验证：

```bash
sudo systemctl status collect_disk_temps.timer --no-pager
sudo systemctl start collect_disk_temps.service
cat /opt/fnos-fan-webui/data/disk-temps.json
```

磁盘保护只会提高风扇输出：

```text
55°C → PWM 180
60°C → PWM 230
65°C → PWM 255
```

不会把自动曲线要求的输出降低。

---

# 四、日常管理

进入目录：

```bash
cd /opt/fnos-fan-webui
```

| 操作 | 命令 |
|---|---|
| 查看状态 | `sudo docker compose ps` |
| 查看最近 100 行日志 | `sudo docker compose logs --tail 100` |
| 实时查看日志 | `sudo docker compose logs -f` |
| 重启服务 | `sudo docker compose restart` |
| 停止服务 | `sudo docker compose down` |
| 启动服务 | `sudo docker compose up -d` |

## 更新镜像

更新前先备份曲线和历史数据：

```bash
cd /opt/fnos-fan-webui
sudo cp -a data "data.backup.$(date +%F-%H%M%S)"
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
curl -fsS http://127.0.0.1:8080/api/status
```

默认版本固定为 `1.0.0`。如确认要跟随最新版，将 Compose 内：

```yaml
image: ghcr.io/wwu-unique/fnos-fan-webui:1.0.0
```

替换成：

```yaml
image: ghcr.io/wwu-unique/fnos-fan-webui:latest
```

再执行更新命令。

---

# 五、故障排除

## 页面打不开

```bash
cd /opt/fnos-fan-webui
sudo docker compose ps
sudo docker port fnos-fan-webui
curl -i http://127.0.0.1:8080/
sudo docker compose logs --tail 100
```

正常标准：

- 容器是 `Up`；
- 端口映射是 `0.0.0.0:8080->8080/tcp`；
- 本机 `curl` 返回 HTTP 200。

若 NAS 本机 HTTP 200、其他设备打不开，检查局域网防火墙、路由隔离、客户端网段与浏览器缓存，不要直接重建容器。

## 模块缺失或 PWM 不可用

```bash
lsmod | grep qnap8528
for h in /sys/class/hwmon/hwmon*; do
  [ -r "$h/name" ] && printf '%s: %s\n' "$h" "$(cat "$h/name")"
done
```

必须看到 `qnap8528`，且对应目录有 `pwm1` 和 `fan1_input`。内核升级后如果节点消失，需要先恢复宿主机模块；Docker 镜像无法修复该问题。

### 内核升级后恢复 qnap8528 驱动（QU-605 实测流程）

fnOS 内核升级后（例如 `6.18.18.c788-trim → 6.18.18.c1032-trim`），旧 `.ko` 的 vermagic 与新版不匹配，`insmod` 会报 `invalid module format`，需要针对当前内核重新编译。以下流程已在 QU-605 上完整验证（2026-08-25）。

**1. 确认源码在宿主机（推荐先查，不要急着上外网）**

```bash
ls /var/lib/fnos-qnap8528-build/src/qnap8528.c
```

> 若没有，从 GitHub 拉取（需能访问外网）：
> ```bash
> git clone https://github.com/ananclub/qnap8528 /var/lib/fnos-qnap8528-build
> ```

**2. 确认当前内核 headers 与编译工具链可用**

```bash
ls /lib/modules/$(uname -r)/build/Makefile
gcc --version && make --version | head -1
```

**3. 解决普通用户无 root 写权限（关键）**

fnOS 普通 SSH 用户无法 `sudo`（sudo 需要独立密码），但 `docker` 组用户可用特权容器以 root 操作：

```bash
docker run --rm -v /:/host alpine chmod -R a+w /host/var/lib/fnos-qnap8528-build/src/
```

**4. 用当前内核 headers 重编**

```bash
cd /var/lib/fnos-qnap8528-build/src
make -C /lib/modules/$(uname -r)/build M=$PWD modules
```

成功后产物为 `qnap8528.ko`（大小会变化，约 597KB）。

**5. 加载并确认 hwmon 节点出现**

```bash
docker run --rm -v /:/host alpine insmod /host/var/lib/fnos-qnap8528-build/src/qnap8528.ko skip_hw_check=true
lsmod | grep qnap8528
for h in /sys/class/hwmon/hwmon*; do [ -r "$h/name" ] && printf '%s: %s\n' "$h" "$(cat "$h/name")"; done
```

正常会新增一个 `hwmonN: qnap8528`，且存在 `pwm1`、`fan1_input`。

**6. 持久化：安装到新内核 extra 目录并 depmod**

```bash
docker run --rm -v /:/host alpine sh -c \
  "mkdir -p /host/lib/modules/$(uname -r)/extra && cp /host/var/lib/fnos-qnap8528-build/src/qnap8528.ko /host/lib/modules/$(uname -r)/extra/ && depmod -b /host $(uname -r)"
```

确认 `/etc/modules-load.d/qnap8528.conf`（内容 `qnap8528`）与 `/etc/modprobe.d/qnap8528.conf`（内容 `options qnap8528 skip_hw_check=true`）已存在；没有则用容器 root 补上。

**7. 重启 fan-webui 容器，验证 RPM 真实回读**

```bash
docker restart fnos-fan-webui
docker logs --tail 5 fnos-fan-webui
```

正常日志会出现 `RPM: 24xx`（不再是 `RPM:0`），WebUI 高级诊断中 `模块：已加载`、`PWM：可读写`。

> **为什么用容器而不是直接 sudo？** QU-605 的 fnOS SSH 用户 sudo 需要 root 密码（与登录密码不同），而 docker 组用户天然可用特权容器以 root 身份操作宿主文件与内核模块——这是该机型上免 root 密码的运维通道。

## RPM 为 0 或明显异常

当前 RPM 换算逻辑只在 QU-605 验证。其它硬件出现异常时，不要以该数值调曲线，先确认 EC 与 `fan1_input` 的实际含义。

## 恢复默认曲线

```bash
cd /opt/fnos-fan-webui
sudo cp data/curve-config.json "data/curve-config.json.backup.$(date +%F-%H%M%S)"
sudo docker compose down
sudo mv data/curve-config.json data/curve-config.json.disabled
sudo docker compose up -d
```

## 8080 端口冲突

检查：

```bash
sudo ss -lntp | grep :8080
```

修改 `docker-compose.yml`：

```yaml
ports:
  - "18080:8080"
```

重启：

```bash
sudo docker compose up -d
```

新地址：

```text
http://NAS_IP:18080/
```

---

# 六、安全说明

本项目包含以下高权限配置：

```yaml
privileged: true
- /sys/class/hwmon:/sys/class/hwmon:rw
```

这是写入物理 PWM 节点所必需的。请遵守：

1. 仅使用本项目 GHCR 镜像；
2. 不要将 8080 端口直接暴露到公网；
3. 需要反向代理时必须增加认证；
4. 首次部署、内核升级、调整曲线后应有人值守；
5. 定期备份 `/opt/fnos-fan-webui/data`。

---

许可证：MIT License。软件按现状提供，使用者自行承担硬件控制、温度阈值、网络暴露与数据备份风险。
