# 学历证书哈希存证合约

本目录是第一版核心合约。链下保存证书正文；链上保存证书 ID、稳定 SHA-256 哈希、状态、签名机构和交易凭据。前端与链下数据库将在链上核心验证后接入。

依赖固定为 `contract-sdk-go/v2 v2.4.0`，无缺失的本地 `replace`。代码按 Go 1.18 构建；使用 `CGO_ENABLED=0` 生成 Linux amd64 二进制，避免依赖宿主 Ubuntu 的 glibc。

`go.sum` 已锁定依赖，所需源码及许可证已收集到 `vendor/`（约 15 MiB）。第四步使用 `-mod=vendor` 和 `GOPROXY=off`，无需在 Ubuntu 联网下载 Go 模块。复制本目录时必须包含整个 `vendor` 子目录。

## 接口

初始化参数为 `issuer_org` 和 `regulator_org`，分别计划映射 `wx-org1.chainmaker.org` 与 `wx-org2.chainmaker.org`。初始化只能成功一次，已有记录不会被再次初始化覆盖。合约创建/升级还受链的管理员签名策略约束。

所有方法参数为字符串键值对，由 CMC `--params` 传入；不额外嵌套 `params` 字段。

| 方法 | 参数 | 权限与结果 |
| --- | --- | --- |
| health | 无 | 返回模式版本和机构配置 |
| issueCertificate | cert_id、content_hash | 仅高校机构的 CLIENT 身份；不允许覆盖已有证书 ID |
| getCertificate | cert_id | 返回链上哈希、状态和交易元数据，不含学生个人信息 |
| verifyCertificate | cert_id、content_hash | 返回 content_match、status、valid；匹配且未撤销时 valid 为 true |
| revokeCertificate | cert_id、reason | 仅监管机构的 CLIENT 身份；状态变为 revoked，原始内容哈希和颁发交易保持不变 |

证书 ID 为 1–64 个 ASCII 字母、数字、下划线或连字符，首字符为字母或数字。哈希是 64 位小写十六进制 SHA-256。撤销原因非空、不含控制字符、最多 256 个 Unicode 字符。

发送者机构、技术角色、交易 ID 和时间均从 SDK 取得；网页提交的角色参数不能替代链上授权。合约不使用本机时间或随机数。证书时间字段是链交易时间戳，尚未定义链下正文的日期校验。

成功响应：`{"ok":true,"data":...}`。失败使用 `sdk.Error`，后端还需检查实际链交易与合约执行码，不能只判断 JSON 是否存在。

## 源码结构与验证

- `core/core.go`：纯业务规则，可用内存存储测试。
- `core/core_test.go`：权限、重复证书、内容改动核验、撤销哈希稳定、多证书独立、参数及读写失败测试。
- `main.go`：ChainMaker SDK 适配及方法路由。

执行 `go test ./core` 验证业务规则；这些测试不替代部署、交易确认、权限身份匹配或共识验证。第四步脚本负责生成仅包含一个二进制文件的 `.7z` 包。

运行时名称必须依据实际链配置选择。v2.4 的 `GO` 与 `DOCKER_GO` 是不同枚举，本步只构建、不部署；不直接照搬旧指导书的 runtime-type。

版本链、批量颁发、链下全文数据库、多证书学生索引和前端页面属于后续阶段，当前尚未实现。
