"""意图四件套标注评估:九类判对率(interrupt 增「人工」)+ confidence 可解析 + 怪问题落其他 + 多轮漂移。需聊天上游可调通。
用法:.venv/bin/python -m scripts.eval_intent"""
import asyncio

from app.core.intent import classify

SAMPLES = [
    ("工单1001的抢修到哪了", "报修"), ("我买的东西装表了吗", "报修"),
    ("工单2002现在什么状态", "工单"), ("我上周下的单多少钱来着", "工单"),
    ("这款电能表多少钱一包", "业务咨询"), ("退费违约金一般谁承担", "业务咨询"),
    ("我要退费", "电费退费"), ("这个工单还能申请退费吗", "电费退费"),
    ("我的用电信息采集终端坏了能换表维护吗", "用电业务"), ("换新进度到哪了", "用电业务"),
    ("你们这什么破服务,我要投诉", "投诉"), ("太差了给我个说法", "投诉"),
    ("帮我建个工单", "人工"), ("电表箱漏电了,帮我建个工单跟进", "人工"),
    ("你好呀", "闲聊"), ("今天天气不错", "闲聊"),
    ("帮我写一段 Python 代码", "其他"), ("阿斯顿发发", "其他"),
]


async def main():
    passed = bad_json = 0
    for q, expect in SAMPLES:
        r = await classify(q)
        got = r.get("intent")
        conf = r.get("confidence")
        ok = got == expect
        json_ok = got in ("报修", "工单", "业务咨询", "电费退费", "用电业务", "投诉", "人工", "闲聊", "其他") \
            and isinstance(conf, float) and 0.0 <= conf <= 1.0
        bad_json += not json_ok
        passed += ok
        print(f"{'✅' if ok else '❌'} {q!r} -> {got}(conf={conf}) 期望={expect}")

    # 多轮漂移:报修→退费→报修,当前句意图应随上下文
    hist = "用户:工单1001到哪了\n客服:已装表,深圳分拨中心\n用户:那我想退了\n客服:好的,帮您看下退费\n"
    r = await classify("那它现在到哪了", hist)
    print(f"多轮漂移『那它现在到哪了』(退费后)-> {r['intent']}(期望 报修)")

    print(f"\n判对 {passed}/{len(SAMPLES)};JSON 越界 {bad_json} 条(glm 非确定性,抖动如实重跑记录)")


if __name__ == "__main__":
    asyncio.run(main())
