"""
飞书机器人测试脚本
用于验证 Webhook 配置是否正确
"""

import requests
import json

# ═══════════════════════════════════════════════════
# 📝 在这里填入你的 Webhook URL
# ═══════════════════════════════════════════════════
FEISHU_WEBHOOK_URL = "https://www.feishu.cn/flow/api/trigger-webhook/7c89a5810f25ae8607ff42fa1f422c9d"

def test_text_message():
    """测试1：发送简单文本消息"""
    print("\n[测试1] 发送文本消息...")
    
    if FEISHU_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print("  ❌ 请先在脚本中填入 FEISHU_WEBHOOK_URL")
        return False
    
    payload = {
        "msg_type": "text",
        "content": {
            "text": "✅ 飞书机器人测试成功！\n这是一条测试消息。"
        }
    }
    
    try:
        resp = requests.post(
            FEISHU_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        result = resp.json()
        
        if result.get('code') == 0:
            print("  ✅ 文本消息发送成功！请检查飞书群。")
            return True
        else:
            print(f"  ❌ 发送失败: {result.get('msg', 'Unknown error')}")
            return False
            
    except Exception as e:
        print(f"  ❌ 发送异常: {e}")
        return False


def test_card_message():
    """测试2：发送交互式卡片（模拟信号）"""
    print("\n[测试2] 发送交互式卡片...")
    
    if FEISHU_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print("  ❌ 请先在脚本中填入 FEISHU_WEBHOOK_URL")
        return False
    
    card = {
        "header": {
            "template": "green",
            "title": {
                "content": "🔔 测试信号 - EURUSD",
                "tag": "plain_text"
            }
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": "**交易方向**\n📈 做多 LONG"
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": "**信号类型**\nH1"
                        }
                    }
                ]
            },
            {
                "tag": "hr"
            },
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": "**🎯 入场价**\n<font color='blue'>1.08500</font>"
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": "**🛡 止损**\n<font color='orange'>1.08300</font>"
                        }
                    }
                ]
            },
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": "**💰 TP1 (1:1)**\n<font color='green'>1.08700</font>"
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": "**🚀 TP2 (2:1)**\n<font color='green'>1.08900</font>"
                        }
                    }
                ]
            },
            {
                "tag": "hr"
            },
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": "**📊 RSI**\n55.3"
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": "**📏 ATR**\n0.00120"
                        }
                    }
                ]
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "⚠️ 这是测试消息，非真实信号"
                    }
                ]
            }
        ]
    }
    
    payload = {
        "msg_type": "interactive",
        "card": card
    }
    
    try:
        resp = requests.post(
            FEISHU_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        result = resp.json()
        
        if result.get('code') == 0:
            print("  ✅ 卡片消息发送成功！请检查飞书群。")
            print("  📱 你应该看到一个绿色的交互式卡片。")
            return True
        else:
            print(f"  ❌ 发送失败: {result.get('msg', 'Unknown error')}")
            return False
            
    except Exception as e:
        print(f"  ❌ 发送异常: {e}")
        return False


def main():
    print("=" * 60)
    print("  飞书机器人配置测试")
    print("=" * 60)
    
    if FEISHU_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print("\n⚠️  请先配置 FEISHU_WEBHOOK_URL")
        print("\n步骤：")
        print("1. 在飞书群中添加自定义机器人")
        print("2. 复制 Webhook 地址")
        print("3. 在本脚本顶部填入 FEISHU_WEBHOOK_URL")
        print("4. 重新运行此脚本")
        print("\n" + "=" * 60)
        return
    
    print(f"\n📍 Webhook URL: {FEISHU_WEBHOOK_URL[:50]}...")
    
    # 测试1：文本消息
    test1_ok = test_text_message()
    
    # 等待用户确认
    if test1_ok:
        input("\n按 Enter 继续测试卡片消息...")
    
    # 测试2：卡片消息
    test2_ok = test_card_message()
    
    # 总结
    print("\n" + "=" * 60)
    print("  测试结果总结")
    print("=" * 60)
    print(f"  文本消息: {'✅ 成功' if test1_ok else '❌ 失败'}")
    print(f"  卡片消息: {'✅ 成功' if test2_ok else '❌ 失败'}")
    
    if test1_ok and test2_ok:
        print("\n🎉 恭喜！飞书机器人配置成功！")
        print("\n下一步：")
        print("1. 复制同样的 Webhook URL")
        print("2. 填入 forex_monitor_feishu.py")
        print("3. 运行监控器: python forex_monitor_feishu.py")
    else:
        print("\n⚠️  配置可能有问题，请检查：")
        print("  - Webhook URL 是否完整")
        print("  - 机器人是否在群聊中")
        print("  - 网络连接是否正常")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
