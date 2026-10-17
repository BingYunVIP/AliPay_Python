# 冰云支付开发约定

用户要求长期执行：分析错误 → 核对参考核心代码 → 规划 → 开发优化 → 自动化测试和浏览器冒烟 → 部署验证 → 清理无用产物 → 记录结果
每次任务在 DEVELOPMENT_LOG.md 记录问题、参考实现、修改、验证证据、发布提交和部署状态（在本机开发环境；VPS 上不要改这个文件，见文末规则）；不得把“付款成功”替代接口、页面及业务流程验收

- 商户模型参考 /root/ByPay/Epay/admin/uset.php?my=api 与 includes/lib/Payment.php、ApiHelper.php、api/Pay.php、functions.php
- 用户要求 V1 商户 ID + MD5、V2 商户 ID + RSA；不要擅自用自定义 Bearer API Key 替代商户接入契约
- V2 商户私钥由商户持有，Pay 保存商户公钥；Pay 私钥签响应/回调，商户使用平台公钥验签
- 测试站创建订单后嵌入 Pay 收银台，由 Pay 生成真实渠道二维码；测试站根据已验证的服务端回调/查询确认成功，浏览器跳转或 postMessage 不是到账凭证
- 本地开发主站端口 888、测试站 8100；同样保留验签、金额/归属校验、幂等和真实状态查询
- VPS 域名：pay.bingyun.vip 属于主站，test.bingyun.vip 属于独立商户测试站，支付宝通知入口为 Pay 的 /alipay/notify
- 不使用 Docker；主站 ByPay.sh 菜单部署，测试站 run.sh 前台运行，停止脚本即停止测试站
- 延续 ByHome_UI / Google_UI/bypay_V1、苹果 HIG、鸿蒙字体设计；避免自行增加宣传段落和装饰；中文界面文案末尾不加句号
- 保留订单、账户、渠道密钥、回调收据和当前运行需要的资源；清理缓存、临时测试产物及超过保留期的轮转日志，不能删当前运行构建或尚需排查的支付日志；data/logs 和 data/local/logs 都是本机服务正在写的日志（测试也会往 data/logs 写），只用 tools/cleanup.py 清理过期轮转文件，不要手工修剪
- 打包独立测试站时仅收录源码，更新 ZIP 与校验文件，不提交环境文件、密钥、运行数据库及日志
- 发布遵循当前会话中的用户授权；真实付款由用户操作，退款不得作为未经单独授权的测试动作
- 部署必须区分“已推送”“已本机验证”“VPS 已执行”“真实付款已验收”，缺少 VPS 连接时提供确切部署产物和交接指令，不虚报上线

开发测试依赖单独列在 requirements-test.txt；浏览器冒烟在具备 Chromium 的隔离测试环境运行 tools/smoke_epay.py，不能用生产数据模拟到账

本轮规划基准见 ByPay_项目规划.md：新增控制台开关必须有后端执行路径和验收测试，金额用整数分，分录只追加并冲正；不能把有页面、模拟支付成功或平衡检查当作审计通过
SMTP / Turnstile 真实验收需用户配置的外部凭据；没有自动出款渠道时只能生成结算申请和登记真实转账凭据，不自动标记转账成功

控制台界面规则（用户逐项验收过，修改时保持；根因和验收见 ByPay_项目规划.md 第 9 节）
- 三段式布局一律用网格 `1fr auto 1fr`，左/中/右固定位置；操作列用三个固定槽位（详情|退款|删除、审核|接口配置|停用），管理员行只保留中间
- 付款卡片是状态机：已支付不显示倒计时和二维码；四种产品共用同一张卡片；手机支付二维码只编码站点地址下的短链接
- 订单选择按订单号保存，搜索、筛选、翻页不得清空；批量/导出只校验归属和列表范围
- 订单详情和确认弹窗是居中弹窗；`<dialog>` 会继承所在表格单元格的 text-align/white-space，必须在弹窗上显式重置
- 不要自行增加说明段落、提示条、刷新按钮；删除用户列出的元素时同时删除前后端无用代码和样式
- 修改界面后运行 `python3 tools/smoke_ui.py`（并按需 smoke_platform / smoke_epay），先构建到临时目录并用 BYPAY_SMOKE_DIST 指向它，部署时才覆盖 frontend/dist

微信支付参考 /root/ByPay/Epay/plugins/wxpayn 与 includes/vendor/cccyun/wechatpay-sdk（V3），根因、参数和流程见 ByPay_项目规划.md 第 10 节
- 只用微信支付公钥模式；保持 TLS 证书校验，不照搬参考实现里关闭校验的做法；notify_url 必须是公网 https，Nginx 要把 /wechat/ 和 /m/ 转发到 Pay
- 微信密钥只由用户填入控制台，不进聊天、日志或提交；证书包里没有微信支付公钥（pub_key.pem 在商户平台 API安全 → 管理公钥）
- 修改微信代码后运行 tools/smoke_wechat.py（进程内模拟网关，不碰真实资金）；tests/wechat_fake.py 的签名和加密格式必须与已用 PHP SDK 交叉验证过的一致
- 密钥格式错误要说明具体原因和字段（证书、公钥、加密私钥、不完整等），不要再返回笼统的“格式不正确”

管理页面规则（系统管理 / 系统日志 / 结算管理，根因和验收见 ByPay_项目规划.md 第 11 节）
- 表单一律用 FormGroup / FormRow / SaveBar：名称在左控件在右，列表类字段在下；保存条没有修改时隐藏，保存必须带服务端 version；错误落到对应字段并聚焦
- 设置分区、日志分类和范围、结算状态写入地址；有未保存修改时离开要询问（console.guard）
- 日志里出现的每个事件代码都要有中文说明；新增 trace 阶段必须加入 web/console.py 的 EVENT_CATEGORIES 和 format.js（有测试保证）
- 修改这三页后运行 tools/smoke_platform.py；共用样式在 console/admin.css，旧的 settings-* / log-* 样式已删除，不要重新引入


商户测试站支付方式与域名验证文件（根因和验收见 ByPay_项目规划.md 第 12 节）
- 测试站（merchant-demo）可选支付宝或微信支付，选择随订单保存，回调和查询返回的 type 必须与下单时一致，不一致不确认到账；微信要求 Pay 的站点地址是公网 https，本机 http 会明确返回 wechat_https_required，不要为了本机能测而放宽
- 修改测试站后：测试站自带测试、tests/test_merchant_demo.py（测试站对真实 Pay 应用，含微信）、tools/smoke_epay.py，再 `python3 tools/package_merchant_demo.py` 重新打包并 `--check`；ZIP 里没有环境文件、密钥、数据库、日志
- 微信 JS 安全域名等平台的验证文件（MP_verify_xxx.txt）放进仓库根目录的 verification/，从站点根路径原样提供：文件名限定为字母数字下划线连字符加 .txt，最大 1 KB，不跟随符号链接，不用重新构建；不要把别的文件放进该目录
- ByPay.sh 菜单 2）更新 与 6）更新代码 是同一套流程（deploy/update.sh）：只允许 fast-forward；先备份数据库、密钥和配置（web.management backup，保留最近 3 份 update-*），再合并和部署，部署失败自动回到更新前的代码和网页文件并重启旧版本（reset 被拒绝时强制回退，代码回退失败也要把服务启动起来）；本机改动的程序文件需用户确认后另存为补丁，不会被静默覆盖；确认之后 Ctrl+C 被忽略、终端断开也走到头，输出经 tee 写入 data/update.log；update.sh 不依赖 set -e，部署放在子进程 bash 里执行，改动后运行 tests/test_update_script.py（含伪终端挂断测试）并对新增保护做变异检查
- 不要在本机直接执行真实 deploy() 或更新菜单（2 / 6）：它们会以 root 覆盖 /etc/systemd/system/bypay.service；本机部署用备份 → 构建 → systemctl restart bypay
- 给 VPS Codex 的提示词必须自包含（ByPay_项目规划.md 第 8 节）：服务器上的代码可能比仓库旧，读不到新文档，所以所有命令、检查和期望值都写进提示词本身，不要让它先去读尚未更新到服务器的文件；写之前用 git log 核对服务器可能缺哪些提交
- VPS 上不要修改仓库里受 git 跟踪的任何文件（包括 DEVELOPMENT_LOG.md 和 AGENTS.md）：本机改动过的文件会挡住 git 快进合并，也会让 ByPay.sh 更新（菜单 2 / 6）停下来询问；VPS 上的部署记录写到仓库之外（/root/bingyun-backups/deploy-notes.md），由本机根据 VPS 的报告补记 DEVELOPMENT_LOG.md；VPS 上也不要做本地 git 提交，否则分支会与线上分叉，更新菜单（2 / 6）将无法自动更新

支付宝二维码出图速度（根因、数据和验收见 ByPay_项目规划.md 第 14 节）
- 网页支付的支付宝二维码要从支付宝收银台页面里取（web/cashier.py），微信在下单的返回里就有；慢在每次新建 TLS 连接，并且等收银台打开后才开始取。已有的做法不要改回去：
  - 订单创建后立即在后台预取（web/cashier.py Prefetch；BYPAY_QR_PREFETCH，由 web/service.py 给网页进程默认打开、给 worker 进程关闭，env 文件写 0 可整体关闭）
  - 所有取二维码的请求共用一个长连接池（CashierFetcher），每次取各用独立的 client，cookie 不在订单之间共享；下单后 10 分钟内由 Warmer 每 25 秒对两个支付宝域名各发一个 HEAD 保温，之后不再向支付宝发任何请求
  - 取二维码同一时间只允许一个请求在飞：用 checkout_jobs 里的租约（Store.claim_qr / release_qr，跨 gunicorn 进程）；其他请求等待结果，不重复取，也不重复在支付宝创建交易
  - prepare / 收银台状态接口直接返回成品图 `qr_svg`（前端用 data: 地址显示，不再请求 qr.svg）；支付宝以外的渠道和手机支付不变
- 取收银台页面时重定向只能去 allowed_url 白名单，并且在发出请求之前检查；总时间、响应大小（含解压后）都有上限；不要改成跟随任意地址，不要读代理环境变量
- VPS 实测（第 14 节）：空闲后的第一笔是冷连接、偏慢（5.1 秒），连接热起来后约 1.2 秒；这是预期的，不要因为第一笔慢就改回“打开收银台才取”或缩短保温窗口去向支付宝长期发请求
- 测试里默认不预取、不保温（测试和工具不能在没有明确要求时联系支付宝）；改动后运行 tests/test_cashier_speed.py 和 tools/smoke_epay.py（含“支付宝慢 1.5 秒 + 网络延迟 150 毫秒”的出图时间对比，预取必须更早出图）；smoke_ui / smoke_platform 现在按 data: 图片读取二维码

本轮（2026-10-18）用户逐项验收过的渠道与商户模板，修改时保持
- 渠道卡片一套模板：正式环境/沙箱徽章放在 `<div class="channel-card-title">` 里 h3 的右侧；只有“配置完整且最近一次检查没有失败”才出现 production 徽章；已配置显示（密钥加密保存、修改配置、检查配置、测试支付），未配置只在右侧保留“配置渠道”
- 检查配置对每个渠道都可用：微信走在线查单，其他渠道按已保存且已校验的配置回答（provider_code=stored_configuration），不要恢复 channel_check_unavailable
- 新增商户字段顺序参考 Epay admin/uset.php?my=add：邮箱（登录账号）、登录密码、网站域名（可带 http(s):// 与路径，保存为纯主机名并可点击）、是否加入回调域名白名单、商户状态、支付权限、商户保证金余额；结算信息暂不设计
- 手续费 = 金额 × fee_rate（默认 1.5%），四舍五入到分，不足 fee_minimum（默认 0.01 元）按最低收；保证金低于 deposit_minimum（默认 10 元）时商户状态默认停用；三者都在系统管理 → 商户与资金规则里改，不要在代码里写死
- 保证金余额只能通过 ledger_lines 追加分录变更（Operations.credit_deposit），不要直接改字段；审核备注默认“商户保证金余额：xxx”，实时读取并写入事件日志
- 用户表列位：用户 | 角色 | 商户 ID | 累计收款 | 成功率 / 订单数 | 状态 | 准入状态 | 操作；订单表列位：商户 ID / 邮箱 | 商品 / 订单号 | 渠道 / 产品 | 实付金额 | 手续费 | 交易状态 | 创建时间 | 支付时间 | 操作
- 商户接口页：接口地址/商户 ID 与签名方式同一行等距；不要恢复 V1/V2 卡片标题（card-heading）、商户私钥说明和 `POST …/mapi.php` 那一行
- 系统管理不要恢复“支付配置检查”分组（SiteSettings），邮箱页不要写 QQ 邮箱默认服务器/授权码说明；删除界面元素时同时删除前后端无用代码、样式与断言
