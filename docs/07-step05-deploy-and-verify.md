# 第 5 步：部署合约并验收真实业务

更新：如果你已经在第一笔 `query-cannot-issue` 处遇到 `合约拒绝原因与预期不一致：Fail`，请改按 [回执修复与恢复说明](08-step05-receipt-fix-and-resume.md) 操作，保留原运行状态，不重新部署。

你反馈的第四步已经通过：Ubuntu 看到 7.7 GiB 内存，三个组织的普通用户身份均可查询真实区块高度 8；Go 核心测试、Linux 编译和 7z 打包成功。本次继续在 `/home/wsc/degree-cert-system` 下工作。

本步会在现有 `chain1` 上创建 `degree_cert_v1` 合约，并发送演示交易。使用虚构学生数据；创建合约后依次验收权限、签发、查询、内容核验及撤销。它仍属于合约联调阶段，浏览器页面在下一步接入。

## 1. 复制本次文件

把 Windows 中的 `degree-cert-system-step05.zip` 复制或拖放到 Ubuntu 的 Downloads 文件夹，确认其位置是：

```text
/home/wsc/Downloads/degree-cert-system-step05.zip
```

在 Ubuntu 终端执行：

```bash
cd /home/wsc/degree-cert-system
unzip -o /home/wsc/Downloads/degree-cert-system-step05.zip -d /home/wsc/degree-cert-system
ls scripts/05-deploy-and-verify.sh
```

新代码放在项目的 `scripts` 目录。压缩包只有脚本、测试及说明，不含私钥、数据库或运行报告。第四步的合约源码和构建包继续使用，不需要重新构建。文档文件名采用 ASCII，减少 Ubuntu 解压时的中文文件名乱码。

## 2. 执行第五步

保持刚刚已经启动的链运行，执行：

```bash
cd /home/wsc/degree-cert-system
sudo -v
bash scripts/05-deploy-and-verify.sh
```

输入 sudo 密码时终端不显示字符是正常现象。**只用 `bash` 执行脚本，不要用 `sudo bash`**：管理员签名材料仍保留在原链目录；脚本只在部署时临时使用 sudo 读取它们。查询、签发、核验和撤销均使用第四步准备的普通用户身份。

若虚拟机此后重新启动过，先用第三步恢复链，再运行第五步：

```bash
cd /home/wsc/degree-cert-system
sudo -v
bash scripts/03-start-and-query.sh
```

第三步确认成功后再执行第五步。无需重做第四步。

## 3. 脚本将做什么

1. 读取本机 CMC 帮助，核对所需参数；重新检查三个身份能否查询 chain1。
2. 校验第四步构建产物及新版 SDK 标记。使用 `latest-build.txt` 指向的文件，并要求二进制与 7z 的 SHA-256 与你反馈的报告一致。
3. 读取链上合约列表；只有确认没有同名合约才创建。部署版本为 `1.0`，配置 org1 签发、org2 监管。已有同名但来源不明时停止，不覆盖或升级。
4. 部署命令填写 `--runtime-type=DOCKER_GO`，随后查询合约元信息和 `health`，验证机构配置及初始化状态。
5. 创建同一虚构学生的证书 A、B；链上仅写入证书 ID、SHA-256、状态和交易元信息，全文保存在项目的运行记录中。
6. 验证查询机构和监管机构不能签发；高校与查询机构不能撤销；重复签发、重复撤销返回合约明确错误。
7. 核验 A 的原始内容为一致、有效，修改专业后的内容为不一致。监管机构撤销 A 后，其原始哈希与签发信息保持不变，核验仍内容一致但已撤销；B 保持有效。

这些操作约发送 10 笔交易（含创建和预期失败的调用）。预期失败必须查询到对应的链上交易及明确的合约错误，不能把网络错误或 CMC 退出失败当作“权限测试通过”。

业务交易发送前会保存交易 ID，再通过 `cmc query tx` 核对链、合约、方法、参数和区块高度。成功写入还会回读证书记录，确认与回执一致。遇到无法确认的结果即停止后续写入。

## 4. 查看并反馈结果

全部成功时会显示：

```text
第五步全部通过：真实合约部署、签发、查询、内容核验、越权拒绝和撤销已完成。
演示证书 A（已撤销）：DEMO_...
演示证书 B（有效）：DEMO_...
结果码：0
报告：/home/wsc/degree-cert-system/reports/stage05-日期时间-随机字符/report.txt
```

请把终端最后输出及该 `report.txt` 内容发回来。报告中的 `通过` 行会说明每项验收结果；只有全部成功才能进入网页接入。若中途停止，也直接反馈当前报告，它会包含停止位置、原因和最近命令输出。

不要为重试而删除 `instance/stage05-state.json`。该文件记录已经发送或尚未确认的交易；执行结果不确定时要先查证，不能盲目重复发送。已经完整通过的第五步可以再次执行，脚本只复查结果，不会重新签发或撤销演示证书。

生成文件：

| 位置 | 用途 |
| --- | --- |
| `reports/stage05-*/report.txt` | 可以直接反馈的执行摘要 |
| `reports/stage05-*/*.txt`、`*.meta.json` | 原始 CMC 回执、命令退出码 |
| `reports/stage05-*/verified-certificates.json` | 最终链上 A、B 记录 |
| `instance/stage05-state.json` | 演示全文、内容哈希、交易 ID、执行进度 |
| `instance/chain-deployment.json` | 全部通过后生成，供后续网页应用读取的链部署信息 |

上述相对位置均位于 `/home/wsc/degree-cert-system` 内。演示记录目前不是网页业务数据库；下一步会建立 SQLite 多证书索引、账号体系及 Flask 页面。

## 5. 运行时参数的依据

SDK 版本、CMC 创建参数和链内部运行时是三个不同概念。本项目使用 `contract-sdk-go/v2 v2.4.0`，但 CMC 创建接口仍传 `DOCKER_GO`。官方 v2.4.0 合约管理源码会拒绝直接传入 `GO`；检测到新版二进制中的 `Contract_SDK_Version` 标记后，在链内部把 `DOCKER_GO` 改为 `GO`。公开查询又可能把内部 `GO` 显示为 `DOCKER_GO`，因此这两个字段不能机械地比较字符串。

依据：[vm-native v2.4.0 contract_manager.go](https://git.chainmaker.org.cn/chainmaker/vm-native/-/raw/v2.4.0/contractmgr/contract_manager.go)，其中创建检查约第 731–743 行、SDK 标记识别约第 1314–1320 行、公开运行时转换约第 338–341 行；[SDK v2.4.0 sdk_system_contract.go](https://git.chainmaker.org.cn/chainmaker/sdk-go/-/raw/v2.4.0/sdk_system_contract.go) 中 `GetContractInfo`/`GetContractList` 说明原生只读查询方式。实际执行仍由本机 CMC 帮助及链回执确认。

## 6. 已完成的本地验证与范围

交付前已运行回执解析与部署编排的离线测试，覆盖截断 JSON、错误状态码、Base64/字符串结果、错误交易 ID、参数不匹配、真实拒绝与网络错误的区分、同名合约保护、执行中断和重复执行；Bash/Python 语法检查通过。

这些测试验证脚本行为，不能替代 Ubuntu 实链验收。本轮没有更改第四步合约源码；真实部署及业务结果以你执行后的第五步报告为准。
