# fnOS Fan WebUI

一个运行在 Docker 中的温度驱动风扇控制服务：读取 CPU、NVMe、主板及可选磁盘温度，按可编辑 PWM 曲线控制宿主机风扇，并提供局域网 Web 控制台。

> **安全边界**：此项目会向宿主机的 Linux `hwmon` PWM 节点写值。错误配置可能导致风扇停转、硬件过热或噪声异常。仅限理解 Linux、Docker 和硬件监控风险的用户使用；请先在有人值守时测试。

## 1. 已验证范围与不适用范围

| 范围 | 状态 | 说明 |
|---|---|---|
| 飞牛 fnOS / flymNAS，QU-605 | **已实测** | Linux 内核通过 `qnap8528` 模块暴露 `pwm1`、`fan1_input`、主板温度节点；本项目当前运行验证基线。 |
| 同类 x86 NAS，已存在 `qnap8528` hwmon 节点 | 条件支持 | 需要用户自行确认风扇 PWM、RPM 换算及温度节点含义；先用手动低风险 PWM 测试。 |
| 任何 Linux 主机，存在兼容 `hwmon` 节点 | 需要移植 | 当前代码自动寻找 `name=qnap8528`；其它驱动需要改写硬件发现和 RPM 读取逻辑。 |
| 威联通 QNAP QTS / QuTS hero 成品 NAS | **未验证，不默认支持** | QTS/QuTS 的内核、容器权限、EC 驱动和 `hwmon` 布局不同；“QNAP”品牌不等于有 `qnap8528` 节点。不要直接照本指南部署。 |
| Docker Desktop、macOS、Windows、虚拟机无物理 PWM | 不支持 | 容器无法控制不存在的宿主机风扇硬件。 |

### 安装前的硬性检查

本项目只在宿主机满足以下命令输出时才可继续：

```bash
for h in /sys/class/hwmon/hwmon*; do
  [ -f "$h/name" ] || continue
  if [ "$(cat "$h/name")" = qnap8528 ]; then
    echo "found: $h"
    ls "$h"/pwm1 "$h"/fan1_input
  fi
done
```

必须能看到：

```text
.../name = qnap8528
.../pwm1
.../fan1_input
```

并建议先只读查看：

```bash
cat /sys/class/hwmon/hwmonX/pwm1
cat /sys/class/hwmon/hwmonX/fan1_input
cat /sys/class/hwmon/hwmonX/temp1_input
```

如果没有这些节点，**停止部署**；先解决内核模块/硬件驱动问题。

## 2. 功能

- 自动温控：CPU 与 NVMe 温度驱动可编辑 PWM 曲线；
- 手动固定输出：用于有人值守时测试；
- 温度稳定性：移动平均、迟滞与 PWM 平滑；
- 磁盘保护：可选磁盘温度采集达到阈值时提升 PWM；
- 实时遥测：CPU、NVMe、主板、硬盘温度，PWM 和实际 RPM；
- 曲线控制台：自动/手动切换、十点 PWM 编辑、保存/放弃；
- 温度历史和 `qnap8528` / PWM 通道诊断。

## 3. 架构

```text
CPU / NVMe / 磁盘温度 ─┐
                       ├─ fan_webui.py 控制循环 ─> qnap8528 pwm1 ─> 风扇
主板温度 / RPM <────────┘              │
                                       └─ HTTP :8080 WebUI + /api/status + /api/log
```

容器不负责安装 `qnap8528`：这是宿主机内核模块，必须先在宿主机正确加载。

## 4. 快速安装（GHCR 镜像）

### 4.1 创建工作目录

```bash
mkdir -p /opt/fnos-fan-webui/data
cd /opt/fnos-fan-webui
curl -fsSLO https://raw.githubusercontent.com/wwu-unique/fnos-fan-webui/main/docker-compose.yml
```

编辑 `docker-compose.yml`，把：

```yaml
image: ghcr.io/wwu-unique/fnos-fan-webui:latest
```

替换为仓库实际 owner，例如：

```yaml
image: ghcr.io/example/fnos-fan-webui:latest
```

### 4.2 启动

```bash
docker compose pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/api/status
```

打开：

```text
http://NAS_LAN_IP:8080/
```

### 4.3 从源码构建

```bash
git clone https://github.com/wwu-unique/fnos-fan-webui.git
cd fnos-fan-webui
mkdir -p data
sed -i 's|^    image:.*|    # image: disabled for local build|' docker-compose.yml
# 在 compose 的 image 行位置改为：build: .
docker compose build --no-cache
docker compose up -d
```

更简单的做法是创建 `compose.override.yml`：

```yaml
services:
  fnos-fan-webui:
    build: .
    image: fnos-fan-webui:local
```

然后：

```bash
docker compose up -d --build
```

## 5. 必须理解的 privileged 权限

Compose 使用：

```yaml
privileged: true
- /sys/class/hwmon:/sys/class/hwmon:rw
```

原因是 Linux 的 PWM 风扇节点在宿主机 sysfs 中，容器需要写入 `pwm1`。这使容器拥有高权限：

- **只能使用可信镜像**；
- 不要将 WebUI 的 `8080` 端口暴露到公网；
- 建议仅绑定受信任 LAN，或在反向代理中加入认证；
- 更新镜像前先阅读版本说明，并在有人值守时观察一次。

## 6. 使用说明

### 自动曲线（推荐）

1. 打开 WebUI；
2. 保持「自动」模式；
3. 逐项调节温度阈值对应的 PWM 百分比；
4. 确认每个后续点不低于前一项，曲线保持平滑上升；
5. 点击「确认应用」；
6. 观察 CPU、NVMe、硬盘温度、PWM 与 RPM 至少 10 分钟。

默认曲线：

| 温度 | PWM |
|---:|---:|
| 35°C | 50 / 255（约 20%） |
| 40°C | 65 / 255（约 25%） |
| 45°C | 80 / 255（约 31%） |
| 50°C | 100 / 255（约 39%） |
| 55°C | 120 / 255（约 47%） |
| 60°C | 150 / 255（约 59%） |
| 65°C | 180 / 255（约 71%） |
| 70°C | 210 / 255（约 82%） |
| 75°C | 235 / 255（约 92%） |
| 80°C | 255 / 255（100%） |

### 手动模式（仅测试）

手动模式会暂停按温度自动调速。只适合有人值守的短时验证：

1. 记录当前 PWM、RPM 和温度；
2. 切换「手动」；
3. 从较安全的中等输出开始，例如 40%–50%；
4. 点击应用后确认 RPM 确实变化；
5. 完成后**立即切回自动模式并应用**。

不要在无人值守时长期保留手动低 PWM。

## 7. 可选：磁盘温度采集

`collect_disk_temps.sh` 需要在宿主机运行，向容器持久数据目录生成 `disk-temps.json`。脚本依赖 `smartctl`。

```bash
sudo install -m 0755 collect_disk_temps.sh /usr/local/sbin/collect_disk_temps.sh
sudo install -m 0644 systemd/collect_disk_temps.service /etc/systemd/system/
sudo install -m 0644 systemd/collect_disk_temps.timer /etc/systemd/system/
```

编辑 service 中的 `DATA_DIR`，必须与 Compose 的 `./data:/data` 对应的宿主机目录一致，例如：

```text
/opt/fnos-fan-webui/data
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now collect_disk_temps.timer
systemctl status collect_disk_temps.timer
cat /opt/fnos-fan-webui/data/disk-temps.json
```

磁盘温度阈值逻辑：55°C→PWM 180、60°C→230、65°C→255；它只会提高输出，不会降低自动曲线所需输出。

## 8. 验收与故障排除

### 容器运行但打不开页面

```bash
docker ps --filter name=fnos-fan-webui
docker port fnos-fan-webui
curl -i http://127.0.0.1:8080/
ss -lntp | grep :8080
docker logs --tail 100 fnos-fan-webui
```

成功标准：容器为 `Up`，本机 HTTP 返回 `200`，端口有 `0.0.0.0:8080->8080/tcp` 映射。

### WebUI 提示模块缺失 / PWM 不可用

```bash
lsmod | grep qnap8528
for h in /sys/class/hwmon/hwmon*; do
  [ -r "$h/name" ] && printf '%s: %s\n' "$h" "$(cat "$h/name")"
done
```

只有当 `qnap8528` 存在，且对应目录有 `pwm1`、`fan1_input`，容器才能控制风扇。

### RPM 不合理或为 0

不同 EC/驱动的 `fan1_input` 语义可能不同。本项目当前采用已验证 QU-605 的换算方式；其它硬件必须自行确认传感器含义，禁止依据错误 RPM 数据调曲线。

### 先检查，再恢复默认

曲线保存在 `./data/curve-config.json`。恢复默认前先备份：

```bash
cp data/curve-config.json data/curve-config.json.backup.$(date +%F-%H%M%S)
```

再停止容器、移走配置、重启：

```bash
docker compose down
mv data/curve-config.json data/curve-config.json.disabled
docker compose up -d
```

## 9. 更新

```bash
cd /opt/fnos-fan-webui
cp -a data "data.backup.$(date +%F-%H%M%S)"
docker compose pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/api/status
```

内核升级后，先验证宿主机的 `qnap8528` 模块仍可加载、`pwm1` 仍存在；镜像更新不能解决内核模块不兼容。

## 10. 开源许可与免责

MIT License。此软件用于实验性硬件控制。使用者对硬件安全、温度阈值、数据备份和网络暴露承担全部责任。
