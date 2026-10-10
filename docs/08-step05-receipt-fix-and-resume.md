# 第五步修复：识别错误正文，核对原交易后继续

## 当前结论

你在 2026-09-27T08:38:44+00:00 的第五步报告中，合约创建、元信息查询和初始化检查均成功。部署交易为：

```text
18d920bdcadda8d3cad0992125931f886f4eb1ee0578400d9ff83e728a4987ca
```

随后 org3 查询身份尝试签发，交易已经进入高度 10 的区块：

```text
692c53c0beaa4c38b936564bdf4626343ef49a4f794d45c8b6a6be72bbac9dba
```

该交易的链状态码为 4，合约状态码为 1，说明调用失败。`message` 是运行时的通用提示 `Fail`，`result` 中的 `VU5BVVRIT1JJWkVE` 解码后为 `UNAUTHORIZED`，是合约明确返回的权限拒绝。原验收脚本只检查了 `message`，因此误报了“拒绝原因不一致”。

这与项目已包含的 SDK 行为一致：`vendor/chainmaker.org/chainmaker/contract-sdk-go/v2/sdk/response.go` 的 `Error` 返回错误信息；`sandbox/tx_handler.go` 将它放入失败结果的 `Result`，并把外层 `Message` 设为 `Fail`。

此次只修复验收脚本，不修改合约，也不需要重新构建、升级或部署。后续签发和撤销尚未实机验收完成。

## 1. 复制修复包

把 `degree-cert-system-step05-fix1.zip` 拖放到 Ubuntu 的 Downloads 文件夹，文件位置应为：

```text
/home/wsc/Downloads/degree-cert-system-step05-fix1.zip
```

保留 Ubuntu 项目中的 `instance`、`config` 和 `reports`。压缩包只更新脚本、测试和说明，不包含运行状态、身份密钥或合约产物。

## 2. 更新并继续

在 Ubuntu 终端执行：

```bash
cd /home/wsc/degree-cert-system
unzip -o /home/wsc/Downloads/degree-cert-system-step05-fix1.zip -d /home/wsc/degree-cert-system
bash scripts/05-deploy-and-verify.sh --resume
```

本次恢复使用普通用户身份，不需要 `sudo -v`，也不要使用 `sudo bash`。

如果虚拟机已经重启、链尚未恢复，先在同一项目目录运行 `sudo -v` 和 `bash scripts/03-start-and-query.sh`，确认第三步成功后再执行上面的恢复命令。链仍在运行时直接恢复即可。

## 3. 恢复流程

脚本只接受本次已确认的部署和第一笔越权调用，或刚完成这笔调用确认的检查点。它会核对原状态中的交易 ID、合约名称、版本、构建哈希、演示全文和内容哈希，并把原始状态文件备份到新报告目录的 `state-before-resume.json`。

随后再次查询链上的合约元信息、初始化状态和原交易，核对发送机构确为 org3，交易的 ID、chain1、合约、方法、完整参数集合及拒绝原因均匹配。只有核对通过，才把原交易标记为已确认并继续剩余八笔演示调用。

它不会再次创建合约，也不会重新发送第一笔已经被拒绝的交易。若查询不成功、状态被改动或存在其他未确认操作，会停止，保留最后一次已保存的执行进度。`--resume` 不是任意未完成交易的自动重试开关。

不要删除或手工修改 `instance/stage05-state.json` 来绕过检查。

## 4. 反馈结果

正常恢复时会出现：

```text
恢复确认：已按交易 ID 核对 org3 的原调用，链上明确拒绝 UNAUTHORIZED；继续后续验收。
通过：query-cannot-issue（已重新查询原交易，未重发）
```

接下来继续检查监管机构不能签发、高校签发 A/B、内容篡改识别、撤销权限、撤销 A 后哈希不变且 B 保持有效。

全部通过后会显示 `第五步全部通过` 和 `结果码：0`。请把终端最后显示的新 `reports/stage05-*/report.txt` 内容发来；如果仍有报错，也反馈新的报告，不需要重发原报告中的证书正文。

恢复补丁已按实际 `Fail + Base64 错误正文` 格式补充解析与恢复流程测试。本地测试验证的是脚本逻辑，Ubuntu 后续业务结果仍以本次运行报告为准。
