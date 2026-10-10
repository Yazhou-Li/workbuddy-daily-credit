# wbipc 协议参考

供接口变更时快速定位与修复。所有信息均从 WorkBuddy 主程序逆向取证并经实测验证。

## 端点发现

配置文件：`~/.workbuddy/wbipc/endpoint.json`

```json
{"endpoint":"/var/folders/.../wbipc/b-<instanceId>.sock","ticket":"<base64url 票据>"}
```

- 主进程每次启动生成新的 `instanceId`，socket 路径随之变化 → **每次运行都要重读**
- `ticket` 用于 HMAC 密钥，重启后也会变
- 端点目录权限要求 0700，socket 文件按 `b-<id>.sock` 命名

## 传输

Unix Domain Socket，JSON 单行帧，以 `\n` 分隔（`Gze` = `JSON.stringify(obj) + "\n"`）。

## 握手流程

```
client → server : session_hello
server → client : session_challenge
client → server : session_prove
server → client : session_hello_ack
```

### 1. session_hello

```json
{
  "type": "session_hello",
  "protocol_min": 1,
  "protocol_max": 1,
  "client_nonce": "<random 32 bytes, base64url>",
  "ticket_id": "<sha256(ticket).hexdigest()[:16]>",
  "client": {"kind": "skill", "id": "daily-credit", "version": "1.0"}
}
```

服务端校验：`ticket_id` 不匹配 → `session_hello_error / auth_failed`。

### 2. session_challenge

```json
{"type":"session_challenge","protocol":1,"server_nonce":"<16B base64url>","server_proof":"..."}
```

### 3. session_prove

```json
{"type":"session_prove","client_proof":"<HMAC-SHA256 base64url>"}
```

签名算法：

```
transcript = ["wbipc-c", protocol, endpoint, clientNonce, serverNonce]
             每项前缀 4 字节大端长度后拼接
proof      = HMAC-SHA256(key = ticket(原始字符串), msg = transcript) → base64url
```

角色标签：client 用 `wbipc-c`，server 用 `wbipc-s`。
注意 **HMAC 密钥是原始 ticket，不是 ticket_id**（`ticket_id` 只用于 hello 阶段身份比对）。

### 4. session_hello_ack

```json
{"type":"session_hello_ack","protocol":1,"connection_epoch":"ce-...",
 "max_inflight":8,"pipes":["wb.entitlement","wb.request"]}
```

`wb.request` 仅在已登录且能解析出后端 baseUrl 时可见 → **可作为登录态判据**。

## JSON-RPC

握手完成后改用标准 JSON-RPC 2.0 帧（`id` 自增）。

| 方法 | 参数 | 说明 |
|---|---|---|
| `broker/GetPipe` | `{"pipe":"wb.request"}` | 返回 `{"channel":"c:wb.request","methods":["http.fetch"]}` |
| `c:wb.request/http.fetch` | 见下 | 需带 `"mode":"call"` |
| `broker/ListPipes` | `{}` | 列出可见 pipe |
| `$/cancel` | `{"id":n}` | 取消请求 |

### http.fetch 参数

```json
{
  "method": "POST",              // GET/HEAD/POST/PUT/PATCH/DELETE
  "path": "/v2/billing/...",     // 必须以 / 开头，禁止 ? # 空格 .. 段
  "query": {"k":"v"},            // 可选，字符串键值
  "headers": {"content-type":"application/json"},  // 仅允许 content-type / accept
  "body_b64": "e30="             // 标准 base64，长度必须 %4==0；GET/HEAD 禁带
}
```

返回：`{"status":200,"headers":{...},"body_b64":"..."}`
响应头仅透传 `content-type / content-length / etag / last-modified / retry-after / location / x-request-id`。
上游超时 50s，响应体上限 655360 字节。

**鉴权由主进程注入**：`authorization: Bearer <token>` + `x-user-id` + `x-enterprise-id`，
调用方无需也无法自行提供。

## 业务接口

### 查询状态

```
POST /v2/billing/meter/checkin-activity-status   body: {}
```

关键字段：

| 字段 | 含义 |
|---|---|
| `active` | 活动是否进行中 |
| `today_checked_in` | 今日是否已领取（幂等判据） |
| `daily_credit` / `today_credit` | 每日额度（服务端下发，不要硬编码） |
| `total_credits` | 累计已领 |
| `streak_days` | 连续签到天数 |
| `start_time` / `end_time` | 活动有效期 |

### 领取

```
POST /v2/billing/meter/daily-checkin   body: {}
```

成功：`{"code":0,"data":{"credit":100,"streak_days":4,"is_streak_day":false}}`

## 错误码

```
E_PIPE_UNKNOWN  E_METHOD_UNKNOWN  E_METHOD_KIND_MISMATCH  E_NOT_DECLARED
E_CONSENT_REQUIRED  E_NOT_CONNECTED  E_POLICY_DENIED  E_UPSTREAM  E_BUSY
E_CHANNEL_UNKNOWN  E_BAD_REQUEST  E_CANCELLED  E_PAYLOAD_TOO_LARGE
E_TIMEOUT  E_INTERNAL
```

## 退出码约定（本 skill）

`0` 成功/已领取 · `1` 失败 · `2` 活动未开启或未登录 · `3` 主进程不可用
