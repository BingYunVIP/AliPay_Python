"""ByPay · 支付宝（AliPay）测试工具

一个面向小白的、可拆分的支付宝支付测试脚本。

模块划分：
    constants.py      常量、路径、配置字段定义（含"在哪里获取"的指导）
    ui.py             终端界面辅助（颜色 / 输入 / 表格）
    crypto.py         本地加密（Fernet + 本机主密钥）
    key_utils.py      密钥格式归一化 + 密钥配对检查
    logger.py         分类日志 + 敏感信息脱敏
    config_store.py   配置读写（加密落盘，避免每次重复填写）
    settings_store.py 运行设置读写
    order_store.py    测试订单记录（并发安全 + 幂等）
    qr_tool.py        支付二维码生成 / 美化 / 解码校验
    alipay_client.py  支付宝官方 SDK 封装
    notify_server.py  本地回调接收服务（notify / return / health）
    tunnel.py         公网隧道（cloudflared），让支付宝能回调到本机
"""

__version__ = "0.1.0"
