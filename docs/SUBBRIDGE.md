# 订阅桥接（可选）

`wb_subbridge.py` 把 Clash / Mihomo 订阅、V2Ray URI 列表或单个 HTTP / SOCKS 代理转换成 mihomo 的 per-node HTTP listener，再把监听端口登记成网关的代理槽。

## 依赖与数据目录

- 解析 Clash YAML 和渲染 `config.yaml` 需要 PyYAML；只使用 V2Ray URI 或 direct proxy 时不会提前导入它。
- 网关默认在仓库目录调用同目录的 `wb_subbridge.py`，可用 `WB_SUBBRIDGE_SCRIPT` 指向其他副本。
- 数据默认放在脚本同目录的 `subbridge-data/`，可用 `WB_SUBBRIDGE_DATA_DIR` 覆盖。
- `WB_SUBBRIDGE_HOST` 同时决定生成 listener 的 `listen` 地址和面板登记槽位时使用的 loopback 地址；未设置时默认 `172.17.0.1`。
- `WB_SUBBRIDGE_PORT_START` / `WB_SUBBRIDGE_PORT_COUNT` 控制端口池（默认从 `17901` 起 64 个）；面板与转换器会读取同一组值。

## Docker

镜像内已安装 PyYAML，但不会自带 mihomo。要真正让这些 listener 工作，需要：

1. 在宿主机运行 mihomo，并把生成的 `config.yaml` 通过 bind mount 交给它；
2. 将 `subbridge-data/` 持久化到宿主机；
3. 让 mihomo 能访问网关登记的 listener 地址和端口。

例如：

```yaml
services:
  wb-proxy:
    image: ghcr.io/ardeyouxipianyi/workbuddy2api-hub:latest
    volumes:
      - ./accounts:/app/accounts
      - ./usage:/app/usage
      - ./subbridge-data:/app/subbridge-data
```

然后由宿主机的 mihomo 读取 `./subbridge-data/config.yaml`。若 mihomo 不在同一网络命名空间，请把 `WB_SUBBRIDGE_HOST` 改成它实际能访问的地址。

## 便携版与 OpenWrt

精简运行时故意不携带 `site-packages`，因此便携版默认没有 PyYAML；面板会明确提示缺少依赖，而不是静默失败。需要 Clash YAML 时，请使用带 PyYAML 的 Python 环境并设置 `WB_SUBBRIDGE_SCRIPT`，或在 OpenWrt 上额外安装 `py3-yaml`。

OpenWrt 的 init 脚本不会自动安装或启动 mihomo；订阅桥接只负责生成配置和登记槽位。
