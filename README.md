# fnOS Fan WebUI

> 面向 **飞牛 fnOS / flymNAS QU-605** 的 Docker 风扇控制面板。它读取宿主机温度并向真实 PWM 节点写入输出，因此这是一个硬件控制项目，不是普通的监控容器。

[![Docker image](https://img.shields.io/badge/GHCR-1.0.0-2496ED?logo=github)](https://github.com/wwu-unique/fnos-fan-webui/pkgs/container/fnos-fan-webui)
[![License](https://img.shields.io/badge/license-MIT-1f883d)](LICENSE)

- **源码仓库**：<https://github.com/wwu-unique/fnos-fan-webui>
- **Docker 镜像**：`ghcr.io/wwu-unique/fnos-fan-webui:1.0.0`
- **WebUI**：`http://NAS_IP:8080/`

---

## 先读：它适不适合你的 NAS？

### 已验证

| 环境 | 结论 |
|---|---|
| 飞牛 fnOS / flymNAS，**QU-605** | ✅ 已在真实硬件上验证：`qnap8528` 驱动、PWM 写入、风扇 RPM、自动曲线和 WebUI 均可用。 |

### 需要自行验证

| 环境 | 结论 |
|---|---|
| 其它 x86 NAS / Linux 主机 | ⚠️ 仅当宿主机已经暴露 `qnap8528`、`pwm1`、`fan1_input` 时才值得尝试。请先完成下方检查。 |
| 其它 hwmon 驱动 | ⚠️ 当前程序自动识别的驱动名是 `qnap8528`；其它驱动需要修改代码后才可能使用。 |

### 不要直接安装

| 环境 | 原因 |
|---|---|
| 威联通 **QNAP QTS / QuTS hero** 成品 NAS | ❌ 未验证。`qnap8528` 是 Linux 驱动名，不代表所有 QNAP 品牌设备都兼容。 |
| Windows、macOS、Docker Desktop、没有物理 PWM 的虚拟机 | ❌ 容器无法控制不存在的宿主机风扇节点。 |

> **风险提示**：容器需要 `privileged: true` 和宿主机 `/sys/class/hwmon` 的读写挂载，才能写入 `pwm1`。错误使用可能造成风扇低转速、过热、噪音异常。只在受信任 LAN 内使用，首次调试请有人值守。

---

# 快速安装（推荐）

下面的流程使用已发布、固定版本的公开镜像 `1.0.0`，不需要 GitHub 登录，也不需要自己编译。

## 第 0 步：确认 Docker Compose 可用

在 fnOS 的 SSH 终端执行：

```bash
docker --version
docker compose version
```

如果第二条命令不可用，先在 fnOS 的 Docker / 1Panel 环境中安装或启用 Docker Compose v2。

## 第 1 步：确认你的硬件节点兼容

**这一步没有通过，不要继续部署。**

```bash
for h in /sys/class/hwmon/hwmon*; do
  [ -r "$h/name" ] || continue
  if [ "$(cat "$h/name")" = "qnap8528" ]; then
    echo "FOUND: $h"
    ls -l "$h/pwm1" "$h/fan1_input"
  fi
done
```

成功时，应看到类似输出：

```text
FOUND: /sys/class/hwmon/hwmon4
... /sys/class/hwmon/hwmon4/pwm1
... /sys/class/hwmon/hwmon4/fan1_input
```

再做一次**只读**确认：

```bash
HWMON=$(for h in /sys/class/hwmon/hwmon*; do
  [ -r "$h/name" ] && [ "$(cat "$h/name")" = qnap8528 ] && echo "$h" && break
done)

cat "$HWMON/pwm1"
cat "$HWMON/fan1_input"
```

- `pwm1` 通常是 `0–255` 的数值；
- `fan1_input` 应该是非零的风扇转速原始值；
- 找不到 `qnap8528`、`pwm1` 或 `fan1_input`：**停止**，先解决宿主机的内核模块 / 驱动问题。

> 容器不安装内核模块，镜像拉取成功也不能补齐缺失的宿主机硬件驱动。

## 第 2 步：下载 Compose 文件

```bash
sudo mkdir -p /opt/fnos-fan-webui/data
cd /opt/fnos-fan-webui
sudo curl -fsSLo docker-compose.yml \
  https://raw.githubusercontent.com/wwu-unique/fnos-fan-webui/main/docker-compose.yml
```

确认镜像版本是固定的 `1.0.0`：

```bash
grep 'image:' docker-compose.yml
```

预期：

```text
image: ghcr.io/wwu-unique/fnos-fan-webui:1.0.0
```

## 第 3 步：启动容器

```bash
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
```

正常时会看到：

```text
fnos-fan-webui   ...   Up   0.0.0.0:8080->8080/tcp
```

## 第 4 步：验收服务与打开页面

在 NAS 本机执行：

```bash
curl -fsS http://127.0.0.1:8080/api/status
```

返回 JSON 且包含温度、PWM、RPM、`system` 等字段即说明服务已启动。

随后从同一局域网浏览器打开：

```text
http://NAS_IP:8080/
```

例如 NAS 地址是 `10.10.10.200`：

```text
http://10.10.10.200:8080/
```

---

# 首次使用：安全地确认风扇控制有效

## 1. 先观察，不要立刻改曲线

打开 WebUI 后确认以下数据都有值：

- 当前控制温度；
- 风扇转速 RPM；
- PWM 输出百分比；
- CPU / 主板 / NVMe 温度；
- 高级诊断中的 `模块：已加载`、`PWM：可读写`。

若 RPM 为 0、PWM 不可读写或模块缺失，先看本文的[故障排除](#故障排除)。

## 2. 自动模式（建议日常使用）

默认是「自动」模式。服务按温度与曲线计算 PWM，同时使用移动平均、迟滞与输出平滑，避免风扇频繁上下跳动。

使用方法：

1. 保持「自动」；
2. 逐项调整温度阈值对应的 PWM；
3. 保持曲线平滑上升——后一个温度点的 PWM 不应低于前一个点；
4. 点击 **确认应用**；
5. 观察至少 10 分钟：温度、PWM 和 RPM 是否合理。

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

默认曲线只是 QU-605 实测起点，不是任何硬件的通用安全阈值。

## 3. 手动模式（仅用于短时验证）

手动模式会暂停自动曲线。只建议在有人值守时用于验证 PWM → RPM 是否有真实响应：

1. 记录切换前的温度、PWM、RPM；
2. 选择「手动」；
3. 从 **40%–50%** 这样的中间值开始；
4. 点击确认应用，观察 RPM 是否随之变化；
5. 测试完成后，**立刻切回自动模式并再次确认应用**。

> 不要在无人值守状态保持手动低 PWM，也不要以手动模式替代长期温控策略。

---

# 可选功能：纳入硬盘 / SSD 温度

容器原生读取 CPU、NVMe 与主板 hwmon 温度。若想额外读取 SATA / SMART 磁盘温度并参与保护策略，可在**宿主机**启用采集定时器。

## 前提

```bash
smartctl --version
```

如果命令不存在，先通过 fnOS 的软件源 / 包管理安装 `smartmontools`。不同 fnOS 版本的包管理方式不同，请按系统实际环境安装。

## 安装采集器

```bash
cd /opt/fnos-fan-webui
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

如果 `disk-temps.json` 有温度数据，刷新 WebUI 后即可看到硬盘温度。

磁盘保护逻辑只会**提高**风扇输出：55°C→PWM 180、60°C→230、65°C→255；不会把自动曲线所需的输出调低。

---

# 日常管理

在 `/opt/fnos-fan-webui` 目录执行。

| 操作 | 命令 |
|---|---|
| 查看状态 | `sudo docker compose ps` |
| 查看最近日志 | `sudo docker compose logs --tail 100` |
| 实时查看日志 | `sudo docker compose logs -f` |
| 重启容器 | `sudo docker compose restart` |
| 停止服务 | `sudo docker compose down` |
| 启动服务 | `sudo docker compose up -d` |

## 更新镜像

更新前先备份配置；风扇曲线与历史数据在 `data/` 目录内。

```bash
cd /opt/fnos-fan-webui
sudo cp -a data "data.backup.$(date +%F-%H%M%S)"
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
curl -fsS http://127.0.0.1:8080/api/status
```

`docker-compose.yml` 默认固定在 `:1.0.0`，所以不会自动切换版本。要跟随最新版本，先阅读项目更新说明，再把镜像标签由：

```yaml
image: ghcr.io/wwu-unique/fnos-fan-webui:1.0.0
```

改成：

```yaml
image: ghcr.io/wwu-unique/fnos-fan-webui:latest
```

随后执行更新命令。

---

# 故障排除

## 页面打不开

在 NAS 执行：

```bash
cd /opt/fnos-fan-webui
sudo docker compose ps
sudo docker port fnos-fan-webui
curl -i http://127.0.0.1:8080/
sudo docker compose logs --tail 100
```

正常标准：

- 容器状态是 `Up`；
- 有 `0.0.0.0:8080->8080/tcp`；
- `curl` 返回 `HTTP/1.0 200 OK` 或 `HTTP/1.1 200 OK`。

若 NAS 本机为 200、其它设备打不开，请检查局域网防火墙、客户端与 NAS 是否处在可互访网段，以及浏览器缓存；不要先重建容器。

## WebUI 显示“模块缺失”或“PWM 不可用”

```bash
lsmod | grep qnap8528
for h in /sys/class/hwmon/hwmon*; do
  [ -r "$h/name" ] && printf '%s: %s\n' "$h" "$(cat "$h/name")"
done
```

必须看到 `qnap8528`，并且对应 hwmon 目录必须存在 `pwm1` 和 `fan1_input`。如果内核升级后不再存在，先恢复/重新适配宿主机模块；Docker 镜像无法解决该问题。

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

> **为什么用容器而不是直接 sudo？** QU-605 的 fnOS SSH 用户 sudo 需要 root 密码（与登录密码不同），而 docker 组用户天然可用特权容器以 root 身份操作宿主文件与内核模块——这是该机型上唯二免 root 密码的运维通道之一。

## RPM 是 0 或明显不合理

`fan1_input` 的含义由 EC 驱动决定。当前 RPM 处理逻辑只在 QU-605 上完成验证。其它设备看到异常 RPM 时，停止依据该数值调曲线，并自行确认硬件传感器语义。

## 误改曲线，如何恢复默认？

先备份，再禁用当前配置：

```bash
cd /opt/fnos-fan-webui
sudo cp data/curve-config.json "data/curve-config.json.backup.$(date +%F-%H%M%S)"
sudo docker compose down
sudo mv data/curve-config.json data/curve-config.json.disabled
sudo docker compose up -d
```

## 8080 端口被占用

```bash
sudo ss -lntp | grep :8080
```

若端口被其它服务使用，请修改 `docker-compose.yml`：

```yaml
ports:
  - "18080:8080"
```

然后重建容器：

```bash
sudo docker compose up -d
```

访问地址改为：`http://NAS_IP:18080/`。

---

# 安全说明

此项目的权限不是普通 Web 应用权限：

```yaml
privileged: true
- /sys/class/hwmon:/sys/class/hwmon:rw
```

这是为了写入 Linux 的物理 PWM 节点。请遵守：

1. 仅使用来自本仓库 GHCR 的镜像；
2. 不要将 8080 直接暴露到公网；
3. 如需反代，必须配置认证；
4. 首次部署、内核升级、曲线调整后，都应有人值守观察；
5. 保留 `data/` 目录备份，避免丢失已验证曲线。

---

## 开源许可

[MIT License](LICENSE)。软件按“现状”提供；使用者自行承担硬件、温度阈值、网络暴露与数据备份风险。
