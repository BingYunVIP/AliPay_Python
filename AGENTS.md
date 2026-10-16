# 冰云支付开发约定

用户要求长期执行：分析错误 → 核对参考核心代码 → 规划 → 开发优化 → 自动化测试和浏览器冒烟 → 部署验证 → 清理无用产物 → 记录结果
每次任务在 DEVELOPMENT_LOG.md 记录问题、参考实现、修改、验证证据、发布提交和部署状态；不得把“付款成功”替代接口、页面及业务流程验收

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

