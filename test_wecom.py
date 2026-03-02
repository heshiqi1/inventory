"""
企业微信机器人测试脚本
用于验证 Webhook 配置是否正确
"""

import requests
from datetime import datetime

# ============================================================
# ★ 填入你的企业微信机器人 Webhook 地址
# ============================================================
WECOM_WEBHOOK_URL = "YOUR_WEBHOOK_URL"   # 替换为实际地址


def test_text_message():
    """测试文本消息"""
    print("正在测试文本消息发送...")
    
    payload = {
        "msgtype": "text",
        "text": {
            "content": f"✅ 企业微信机器人测试消息\n发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        }
    }
    
    try:
        resp = requests.post(
            WECOM_WEBHOOK_URL, 
            json=payload, 
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        resp.raise_for_status()
        result = resp.json()
        
        if result.get('errcode') == 0:
            print("✅ 文本消息发送成功！")
            return True
        else:
            print(f"❌ 文本消息发送失败: {result.get('errmsg', 'Unknown error')}")
            return False
    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return False


def test_markdown_message():
    """测试 Markdown 消息"""
    print("\n正在测试 Markdown 消息发送...")
    
    markdown_content = f"""## 🔔 企业微信 Markdown 测试

> **测试项目**: H1/H2 外汇信号监控器
> **测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

### 📊 支持的格式

> **加粗文本**: 使用 **双星号**
> **颜色标签**: 
> <font color="info">绿色文本</font>
> <font color="comment">灰色文本</font>
> <font color="warning">橙色文本</font>

### 📈 模拟交易信号

> 🎯 **入场价**: <font color="info">1.05432</font>
> 🛡 **止损价**: <font color="warning">1.04982</font>
> 💰 **目标价**: <font color="comment">1.05882</font>

---
✅ 如果你看到这条消息，说明配置成功！"""

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": markdown_content
        }
    }
    
    try:
        resp = requests.post(
            WECOM_WEBHOOK_URL, 
            json=payload, 
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        resp.raise_for_status()
        result = resp.json()
        
        if result.get('errcode') == 0:
            print("✅ Markdown 消息发送成功！")
            return True
        else:
            print(f"❌ Markdown 消息发送失败: {result.get('errmsg', 'Unknown error')}")
            return False
    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return False


def test_signal_format():
    """测试完整的信号格式（与实际监控器相同）"""
    print("\n正在测试完整信号格式...")
    
    signal_markdown = """## 🔔 H1/H2 新信号 - XAUUSD

> **交易方向**: 📈 做多 LONG <font color="info">多单</font>
> **信号类型**: H1
> **信号日期**: 2026-03-02

### 📊 交易参数
> 🎯 **入场价**: <font color="info">2045.50</font>
> 🛡 **止损价**: <font color="warning">2042.30</font>
> 💰 **TP1 (1:1)**: <font color="comment">2048.70</font>
> 🚀 **TP2 (2:1)**: <font color="comment">2051.90</font>

### 📈 技术指标
> 📊 **RSI**: 58.3
> 📏 **ATR**: 3.20
> 💵 **当前收盘**: 2044.80

⏰ 扫描时间: 2026-03-02 14:30"""

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": signal_markdown
        }
    }
    
    try:
        resp = requests.post(
            WECOM_WEBHOOK_URL, 
            json=payload, 
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        resp.raise_for_status()
        result = resp.json()
        
        if result.get('errcode') == 0:
            print("✅ 完整信号格式发送成功！")
            return True
        else:
            print(f"❌ 完整信号格式发送失败: {result.get('errmsg', 'Unknown error')}")
            return False
    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return False


def main():
    print("=" * 60)
    print("  企业微信机器人测试工具")
    print("=" * 60)
    
    if WECOM_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print("\n❌ 错误: 请先配置企业微信 Webhook 地址！")
        print("   在脚本开头找到 WECOM_WEBHOOK_URL 并填入你的地址")
        print("\n获取方式：")
        print("1. 在企业微信群中点击「...」→「添加群机器人」")
        print("2. 创建机器人后复制 Webhook 地址")
        print("3. 地址格式: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...")
        return
    
    print(f"\n📍 Webhook 地址: {WECOM_WEBHOOK_URL[:50]}...")
    print("\n开始测试...\n")
    
    # 测试1: 文本消息
    test1 = test_text_message()
    
    # 测试2: Markdown 消息
    test2 = test_markdown_message()
    
    # 测试3: 完整信号格式
    test3 = test_signal_format()
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("  测试结果汇总")
    print("=" * 60)
    print(f"  文本消息:       {'✅ 通过' if test1 else '❌ 失败'}")
    print(f"  Markdown消息:   {'✅ 通过' if test2 else '❌ 失败'}")
    print(f"  完整信号格式:   {'✅ 通过' if test3 else '❌ 失败'}")
    print("=" * 60)
    
    if test1 and test2 and test3:
        print("\n🎉 所有测试通过！企业微信机器人配置正确！")
        print("   现在可以运行 forex_monitor_feishu.py 开始监控了。")
    else:
        print("\n⚠️  部分测试失败，请检查：")
        print("   1. Webhook 地址是否正确")
        print("   2. 网络连接是否正常")
        print("   3. 机器人是否被移除")
        print("   4. 查看上面的错误信息")


if __name__ == "__main__":
    main()
