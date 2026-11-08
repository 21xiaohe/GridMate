"""context 摘要 prompt 标注样例验证(纯 Prompt 任务以 eval 代 TDD)。需聊天上游可调通。
断言:严格 JSON(structured output 保证)、关键事实保留、无编造实体、长度达标、寒暄不留。
用法:uv run python -m scripts.eval_summarizer"""
import asyncio
import re

from app.core.summarizer import summarize_dialog

# 样例1:事实保留——工单号/手机号/诉求必须进摘要
CASE1_DIALOG = """用户:你好在吗
客服:您好,我是电小智,请问有什么能帮您?
用户:我买的用电信息采集终端工单1001一直没到,什么时候装表
客服:工单1001已从杭州仓发出,预计后天到。
用户:太慢了,我手机13800138000,到了让抢修提前打电话
客服:好的,已备注让抢修员派送前致电13800138000。
用户:对了这个用电信息采集终端承重多少
客服:这款用电信息采集终端最大承重15公斤。"""
CASE1_MUST = ["1001", "13800138000", "用电信息采集终端"]
CASE1_BAN = ["在吗", "您好,我是电小智"]          # 寒暄不留

# 样例2:无编造——摘要里的数字实体必须都在原文出现过
CASE2_DIALOG = """用户:工单2002能退吗
客服:工单2002是电能表,已签收3天,符合7天无理由,可以退。
用户:那我要退,原因是电表走字不准
客服:好的,已为您记录退费诉求:工单2002,原因「电表走字不准」。"""

# 样例3:滚动合并——旧摘要里的事实不得丢
CASE3_OLD = "用户问过工单1001(用电信息采集终端)报修,留了手机13800138000要求抢修先打电话;问过承重(15公斤)。"
CASE3_DIALOG = """用户:用电信息采集终端到了,但是少了一根立柱
客服:非常抱歉!可为您补发立柱,或整单退费,您选哪种?
用户:补发吧
客服:好的,已登记工单1001补发立柱,3天内发出。"""
CASE3_MUST = ["1001", "13800138000", "立柱"]     # 旧事实(手机号)+ 新事实(补发立柱)


def check(name: str, summary: str, must=(), ban=(), src_digits: str = "") -> bool:
    ok = True
    problems = []
    if not (20 <= len(summary) <= 250):
        ok, problems = False, problems + [f"长度{len(summary)}出界[20,250]"]
    for kw in must:
        if kw not in summary:
            ok, problems = False, problems + [f"关键事实丢失:{kw}"]
    for kw in ban:
        if kw in summary:
            ok, problems = False, problems + [f"寒暄残留:{kw}"]
    if src_digits:
        src_nums = set(re.findall(r"\d{4,}", src_digits))
        for n in set(re.findall(r"\d{4,}", summary)):
            if n not in src_nums:
                ok, problems = False, problems + [f"编造数字实体:{n}"]
    print(f"{'✅' if ok else '❌'} {name} len={len(summary)}")
    print(f"   摘要:{summary}")
    if problems:
        print(f"   问题:{problems}")
    return ok


async def main():
    results = []
    s1 = await summarize_dialog("", CASE1_DIALOG)
    results.append(check("样例1 事实保留+寒暄不留", s1, must=CASE1_MUST, ban=CASE1_BAN,
                         src_digits=CASE1_DIALOG))
    s2 = await summarize_dialog("", CASE2_DIALOG)
    results.append(check("样例2 无编造(数字实体⊆原文)", s2, must=["2002"],
                         src_digits=CASE2_DIALOG))
    s3 = await summarize_dialog(CASE3_OLD, CASE3_DIALOG)
    results.append(check("样例3 滚动合并旧事实不丢", s3, must=CASE3_MUST,
                         src_digits=CASE3_OLD + CASE3_DIALOG))
    print(f"\n{sum(results)}/{len(results)} 通过")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
