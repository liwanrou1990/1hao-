"""iphone_farm：Mac + Sipolar A-832 + 多台 iPhone 群控（基于 YueChen-C/iPhone-use HTTP API）。

硬性原则：
- 每个动作必须指定完整的真实设备 ID（capture_id），客户端拒绝空设备；
- 响应中的 device 必须与请求一致，否则判定为“目标不一致”并记录错误；
- 模拟服务器（mock_server）产生的任何结果都带 SIMULATED 标签，不得当作真实设备结果汇报。
"""

__version__ = "0.1.0"
