"""
飞书流程自动化 Webhook 测试脚本
用于验证优化后的 JSON 格式
"""

import sys
import requests
import json
from collections import OrderedDict
from datetime import datetime

# 修复 Windows 控制台 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ═══════════════════════════════════════════════════
# 📝 在这里填入你的流程自动化 Webhook URL
# ═══════════════════════════════════════════════════
FEISHU_WEBHOOK_URL = "https://www.feishu.cn/flow/api/trigger-webhook/7c89a5810f25ae8607ff42fa1f422c9d"


def test_flow_signal():
    """测试1：发送模拟交易信号（优化格式）"""
    print("\n[测试1] 发送流程自动化格式的交易信号...")
    
    if FEISHU_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print("  ❌ 请先在脚本中填入 FEISHU_WEBHOOK_URL")
        return False
    
    # 使用 OrderedDict 保证字段顺序
    signal_data = OrderedDict([
        ("title", "🔔 H1/H2 新信号"),
        ("symbol", "EURUSD"),
        ("direction", "做多 LONG 📈"),
        ("signal_type", "H1"),
        ("signal_date", "2026-03-02"),
        ("entry_price", 1.08500),
        ("stop_loss", 1.08300),
        ("take_profit_1", 1.08700),
        ("take_profit_2", 1.08900),
        ("risk_reward_1", "1:1"),
        ("risk_reward_2", "1:2"),
        ("rsi", 55.3),
        ("atr", 0.00120),
        ("current_price", 1.08450),
        ("scan_time", datetime.now().strftime('%Y-%m-%d %H:%M'))
    ])
    
    # 直接发送 JSON（流程自动化格式）
    payload = dict(signal_data)
    
    print("\n发送的 JSON 数据：")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    
    try:
        resp = requests.post(
            FEISHU_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        result = resp.json()
        
        print(f"\n响应状态码: {resp.status_code}")
        print(f"响应内容: {json.dumps(result, indent=2, ensure_ascii=False)}")
        
        if result.get('code') == 0 or result.get('success') == True:
            print("\n  ✅ 流程自动化消息发送成功！")
            print("  📱 请检查飞书流程是否接收到数据。")
            return True
        else:
            print(f"\n  ❌ 发送失败: {result}")
            return False
            
    except Exception as e:
        print(f"\n  ❌ 发送异常: {e}")
        return False


def test_flow_text():
    """测试2：发送简单文本消息"""
    print("\n[测试2] 发送流程自动化文本消息...")
    
    if FEISHU_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print("  ❌ 请先在脚本中填入 FEISHU_WEBHOOK_URL")
        return False
    
    payload = {
        "message_type": "text",
        "content": "✅ 流程自动化测试消息\n这是一条简单的文本通知。"
    }
    
    print(f"\n发送的数据: {json.dumps(payload, indent=2, ensure_ascii=False)}")
    
    try:
        resp = requests.post(
            FEISHU_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        result = resp.json()
        
        print(f"\n响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
        
        if result.get('code') == 0 or result.get('success') == True:
            print("\n  ✅ 文本消息发送成功！")
            return True
        else:
            print(f"\n  ❌ 发送失败: {result}")
            return False
            
    except Exception as e:
        print(f"\n  ❌ 发送异常: {e}")
        return False


def test_multiple_signals():
    """测试3：发送多个信号（模拟批量）"""
    print("\n[测试3] 发送多个交易信号...")
    
    signals = [
        {
            "symbol": "USDJPY",
            "direction": "做空 SHORT 📉",
            "entry": 145.20,
            "stop": 145.50
        },
        {
            "symbol": "GBPUSD",
            "direction": "做多 LONG 📈",
            "entry": 1.2650,
            "stop": 1.2620
        }
    ]
    
    success_count = 0
    
    for i, sig in enumerate(signals, 1):
        print(f"\n  发送信号 {i}/{len(signals)}: {sig['symbol']}...")
        
        signal_data = OrderedDict([
            ("title", f"🔔 测试信号 #{i}"),
            ("symbol", sig["symbol"]),
            ("direction", sig["direction"]),
            ("entry_price", sig["entry"]),
            ("stop_loss", sig["stop"]),
            ("test_mode", True)
        ])
        
        try:
            resp = requests.post(
                FEISHU_WEBHOOK_URL,
                json=dict(signal_data),
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            result = resp.json()
            
            if result.get('code') == 0 or result.get('success') == True:
                print(f"    ✅ {sig['symbol']} 发送成功")
                success_count += 1
            else:
                print(f"    ❌ {sig['symbol']} 发送失败")
        except Exception as e:
            print(f"    ❌ {sig['symbol']} 异常: {e}")
    
    print(f"\n  成功: {success_count}/{len(signals)}")
    return success_count == len(signals)


def main():
    print("=" * 60)
    print("  飞书流程自动化 Webhook 测试")
    print("=" * 60)
    
    if FEISHU_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print("\n⚠️  请先配置 FEISHU_WEBHOOK_URL")
        print("\n步骤：")
        print("1. 确认你使用的是飞书流程自动化 Webhook")
        print("2. URL 格式：https://www.feishu.cn/flow/api/trigger-webhook/...")
        print("3. 在本脚本顶部填入 FEISHU_WEBHOOK_URL")
        print("4. 重新运行此脚本")
        print("\n" + "=" * 60)
        return
    
    print(f"\n📍 Webhook URL: {FEISHU_WEBHOOK_URL[:60]}...")
    print(f"📍 类型: 飞书流程自动化")
    
    # 测试1：交易信号
    test1_ok = test_flow_signal()
    
    if test1_ok:
        input("\n按 Enter 继续测试文本消息...")
    
    # 测试2：文本消息
    test2_ok = test_flow_text()
    
    if test2_ok:
        input("\n按 Enter 继续测试批量信号...")
    
    # 测试3：批量信号
    test3_ok = test_multiple_signals()
    
    # 总结
    print("\n" + "=" * 60)
    print("  测试结果总结")
    print("=" * 60)
    print(f"  交易信号格式: {'✅ 成功' if test1_ok else '❌ 失败'}")
    print(f"  文本消息:     {'✅ 成功' if test2_ok else '❌ 失败'}")
    print(f"  批量信号:     {'✅ 成功' if test3_ok else '❌ 失败'}")
    
    if test1_ok and test2_ok:
        print("\n🎉 恭喜！流程自动化 Webhook 配置成功！")
        print("\n✨ JSON 格式说明：")
        print("   - 使用有序字典，字段按重要性排列")
        print("   - 字段命名清晰（entry_price, stop_loss 等）")
        print("   - 数字保留适当精度")
        print("   - 包含 Emoji 便于识别方向")
        print("\n📋 下一步：")
        print("1. 在飞书流程中配置格式化节点")
        print("2. 将 JSON 数据转换为美观的消息")
        print("3. 发送到指定群聊或个人")
        print("\n💡 提示：")
        print("   如果想要更美观的卡片，建议切换到飞书自定义机器人")
        print("   自定义机器人支持交互式卡片，有颜色、图标、分栏布局")
    else:
        print("\n⚠️  部分测试失败，请检查：")
        print("  - Webhook URL 是否正确")
        print("  - 流程是否启用")
        print("  - 网络连接是否正常")
        print("  - 查看上方的错误详情")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
