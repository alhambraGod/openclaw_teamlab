# TeamLab API 调用规范

## Base URL（固定，不使用变量）

```
http://127.0.0.1:10301
```

> **注意**：不要使用 `${TEAMLAB_BASE_URL:-...}` 这类 bash 默认值语法，OpenClaw 不支持，会在 URL 中留下 `}` 导致请求失败。

## 禁止使用的地址

- `claw-teamlab:10301` — Docker 内部 DNS，宿主机无法解析
- `csi-teamlab` — 旧容器名，已废弃
- `host.docker.internal` — 不可靠
- 任何 `172.x.x.x` Docker bridge 地址

## 正确示例

```bash
curl -s "http://127.0.0.1:10301/api/agent/team-overview"
curl -s "http://127.0.0.1:10301/api/collaborations/network"
curl -s "http://127.0.0.1:10301/api/coevo/members"
curl -s "http://127.0.0.1:10301/api/agent/best-collaborators?name=张旭华&top=5"
curl -s "http://127.0.0.1:10301/api/agent/person-context?name=甄园宜"
```

## 说明

TeamLab 后端（claw-teamlab Docker 容器）将端口 10301 映射到宿主机，因此
从 OpenClaw 进程（宿主机运行）始终可通过 `127.0.0.1:10301` 访问，无需任何变量替换。
