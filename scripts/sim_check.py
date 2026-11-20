#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YC-FX-skill 交稿前机检（守门员）——所有文体通用，一条命令跑完全部检查。

用法：
    python sim_check.py <原文文件> <新稿文件>
    python sim_check.py <原文文件> <新稿文件> --banned "一千块,月饼,金项链"
    python sim_check.py <原文文件> <新稿文件> --quiet     # 只输出最后一行结论

检查项（自动按文体切换）：
    · 字数 —— 原文±10% 区间，新稿是否落区间
    · 重合 —— 连续 6 字以上完全重合（应为 0）；4—5 字重合清单（逐条判断）
    · 套话 —— 黑名单三分位命中（开头位／结尾位命中即 FAIL）
    · 禁列词 —— 命令行传入的原文高辨识度词（金额、专有物、特色句），命中即 FAIL
    · 骨架 —— 罗列式查条数／编号／排版／数量承诺／编号写法（数量承诺只认"与条数相符"
               的数字，量词可换；编号只比阿拉伯／中文大类，分隔符号不逐字比对；行首编号
               若写成半角"1. "会被 markdown 当有序列表、复制时丢号，判 FAIL）；故事型查段数
    · 人工特征 —— 仅故事类（叙事型／观察型／论述型）。查分析词、情绪标签成语、
                  工整排比、破折号次数、结尾抽象总结句（以上命中即 FAIL），
                  并给出句长标准差／段落长短／填充词／模糊量词／感叹号／生僻字参考值。
                  罗列式不做朱雀处理，此项直接跳过。规则见 story/anti-ai-story.md。

结论只有 PASS / FAIL。退出码 0 = PASS，1 = FAIL，2 = 用法错误。
脚本是守门员，不是得分手：它不降相似度，只把漏检从"靠人眼比对"变成"每次必跑"。
"""
import argparse
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 编号识别：覆盖罗列式常见的全部序号形态——
#   阿拉伯：1、／1.／1，／1）
#   中文：一、／一，；第1，；第一、／第一，／第一点：
# 关键点：带"第"的形态必须有分隔标记（、，,：:）或量词（点／条／则／项）收口，
# 否则"第一次……"这类叙事句会被误判成条目，整篇文体判定跟着翻车。
NUM_RE = re.compile(
    r"^\s*(?:"
    r"第\s*\d{1,2}(?!\d)\s*[.、．，,：:)）]"
    r"|(\d{1,2})\s*[.、．，,：:)）]"
    r"|第\s*[一二三四五六七八九十]{1,3}\s*(?:[、，,：:]|[点条则项]\s*[、，,：:]?)"
    r"|其[一二三四五六七八九十]{1,3}\s*[、，,：:]"
    r"|[一二三四五六七八九十]{1,3}\s*(?:[、，,：:]|[点条则项]\s*[、，,：:]?)"
    r")"
)
# 行首编号若写成"数字＋半角点／半角右括号＋空白"，markdown 会当成有序列表渲染 ——
# 编号由渲染器生成、不是正文字符，复制到别处（头条编辑器、文档）时会丢号。
# 罗列式一律改用中文顿号（1、／一、）收口，编号才复制得走。
COPY_UNSAFE_RE = re.compile(r"^\s*\d{1,9}[.)](?=\s|$)")
COMMIT_RE = re.compile(r"(\d{1,2}|[一二三四五六七八九十百]{1,3})\s*(种|条|个|句|件|点|样|招|大)")
CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100}
WORD_COUNT_RE = re.compile(r"字数\s*=\s*\d+\s*字")
MONEY_RE = re.compile(r"\d{2,}(?:\.\d+)?\s*(?:块|元|万|千|百|毛|角|块钱)")

# ── 人工特征（朱雀）检测用词表：只用于故事类，罗列式跳过 ─────────────
ANALYSIS_WORDS = ["彰显", "体现了", "体现出", "标志着", "意味着", "诠释",
                  "折射出", "由此可见", "值得一提", "说白了", "究其原因"]
CLICHE_IDIOMS = ["五味杂陈", "百感交集", "心如刀割", "感慨万千", "热泪盈眶",
                 "思绪万千", "如释重负", "浮想联翩", "感慨不已", "心中五味"]
PARALLEL_PATTERNS = [
    (re.compile(r"不是[^。！？；\n]{2,18}而是"), "不是…而是…"),
    (re.compile(r"不仅[^。！？；\n]{2,18}而且"), "不仅…而且…"),
    (re.compile(r"既[^。！？；\n]{2,14}又[^。！？；\n]{2,14}"), "既…又…"),
    (re.compile(r"与其[^。！？；\n]{2,18}不如"), "与其…不如…"),
]
ABSTRACT_TAIL = ["说到底", "归根结底", "这才明白", "总算是看透", "算是看透了",
                 "才是最重要的", "才是最难得的"]
FILLER_WORDS = ["讲老实话", "老实说", "心想", "并不是", "的时候", "里头",
                "老半天", "反正", "寻思", "压根", "要不", "谁知道",
                "结果呢", "后来呢", "你说这", "也不是说"]
# 模糊数量词的识别**不按"词表"枚举写法**，按"约数标记"识别——
# 历史教训：枚举写法三次漏检（"五十来岁""小半个月""两个来钟头""五点多"），
# 因为口语约数形态是无穷的。机理是"数字表达式里含约数标记"，按这个写一条就够。
_NUM = r"[0-9〇零一二两三四五六七八九十百千万]"
VAGUE_QUANT_RE = re.compile(
    # 一、数字 ＋ 约数标记（来／几／多／余／把），中间最多夹两个字：
    #     十来个／两个来钟头／五点多／三十几岁／百把块
    _NUM + r"[^0-9〇零一二两三四五六七八九十百千万，。！？；：、\s]{0,2}(?:来|几|多|余|把)|"
    # 二、数字 ＋ 概数尾巴（左右／上下／前后／出头／开外／挂零）
    + _NUM + r"[^，。！？；：、\s]{0,2}(?:左右|上下|前后|出头|开外|挂零)|"
    # 三、概数词本身
    + r"差不多|大概|大约|估摸着|没听清|记不清|说不准|也许|好像|似乎|"
    + r"好几|几十|十几|几百|几千|几万|上百|上千|成千|好些|若干|个把|"
    # 四、并列概数（后接量词才算，免得"乱七八糟"之类误命中）：两三个／三四趟／十二三岁
    + r"(?:一两|两三|三四|四五|五六|六七|七八|八九|十二三|十五六)"
    + r"(?:个|十|百|千|万|岁|年|天|月|日|块|斤|里|回|次|趟|人|只|件|条|家|位|名|步|点钟)|"
    # 五、半个／小半个月／快四十
    + r"[小大]?半[个]?[岁年天月块万千百个斤里回次趟辈载]|"
    + r"快[一二两三四五六七八九十百千万\d]+(?:[岁年天月块万千百个斤里回次趟]|了|多)")
SENT_SPLIT_RE = re.compile(r"[。！？；!?;\n]+")


def cn2num(s):
    if s.isdigit():
        return int(s)
    if "十" in s:
        head, _, tail = s.partition("十")
        tens = CN_DIGIT.get(head, 1) if head else 1
        ones = CN_DIGIT.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return CN_DIGIT.get(s, 0)


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return WORD_COUNT_RE.sub("", f.read())


def nospace(text):
    return re.sub(r"\s+", "", text)


def plain(text):
    return re.sub(r"[^\u4e00-\u9fff0-9A-Za-z]", "", text)


def count_chars(text):
    """统计口径：正文总字符数（含中文标点，不含空格、换行）。"""
    return len(re.sub(r"[\s\n\r]", "", text))


def lines_of(text):
    return [l.strip() for l in text.splitlines() if l.strip()]


def longest_common_spans(ref, new, minlen):
    spans = []
    j, n = 0, len(new)
    while j < n:
        best, k = 0, minlen
        while k <= n - j:
            if new[j:j + k] in ref:
                best = k
                k += 1
            else:
                break
        if best >= minlen:
            spans.append((j, best, new[j:j + best]))
            j += best
        else:
            j += 1
    return spans


def scan_skeleton(text):
    lines = lines_of(text)
    items, style = [], None
    for idx, l in enumerate(lines, 1):
        m = NUM_RE.match(l)
        if not m:
            continue
        items.append((idx, len(nospace(l)), l))
        if style is None:
            style = "arabic" if m.group(1) else "cn"
    if items:
        per_item = len(lines) / float(len(items))
        layout = "连写式" if per_item < 1.8 else "分行式"
    else:
        per_item, layout = 0.0, "无编号"
    unsafe_sep = [i for i, l in enumerate(lines, 1) if COPY_UNSAFE_RE.match(l)]
    # 数量承诺＝"条数承诺"，只认与全篇条目数相符的那个数字。
    # 开头行里顺带出现的数量词（"一个家""两件事"）不算承诺——否则新稿把条数写对了，
    # 反而会被这些顺带的数词顶掉（旧实现取首个匹配，必中第一个数词），判成 FAIL。
    first = lines[0] if lines else ""
    commit_sig, commit_show = None, "无"
    if items:
        for num_s, unit in COMMIT_RE.findall(nospace(first)):
            if cn2num(num_s) == len(items):
                commit_sig = (cn2num(num_s), unit)
                commit_show = "%d%s" % commit_sig
                break
    return {
        "lines": len(lines),
        "items": len(items),
        "style": {"arabic": '阿拉伯数字（1、／1.）', "cn": "中文数字（一、／第一，）", None: "无"}[style],
        "layout": layout,
        "per_item": round(per_item, 2),
        "commit": commit_show,
        "commit_sig": commit_sig,
        "unsafe_sep": unsafe_sep,
    }


def parse_blacklist(path):
    zones = {"开头位": [], "结尾位": [], "解释位": []}
    if not os.path.isfile(path):
        return zones
    cur = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("##"):
                cur = None
                for z in zones:
                    if z in line:
                        cur = z
                        break
                continue
            if cur and line.startswith("- "):
                body = line[2:].strip().rstrip("。")
                for part in re.split(r"[／/]", body):
                    part = part.strip().strip("。").strip()
                    if part and part not in zones[cur]:
                        zones[cur].append(part)
    return zones


def check_blacklist(text, zones):
    flat = nospace(text)
    hits = []
    for zone, phrases in zones.items():
        for p in phrases:
            if p.endswith("……"):
                key = p[:-2]
                if key and key in flat:
                    hits.append((zone, p))
            elif "……" in p:
                head, tail = p.split("……", 1)
                if head in flat and tail and flat.find(head) < flat.find(tail):
                    hits.append((zone, p))
            elif p in flat:
                hits.append((zone, p))
    return hits


def ref_risk_terms(ref_text):
    """从原文里挑出最容易撞的高辨识度内容：金额串 + 引号内短句。"""
    terms = []
    for m in MONEY_RE.finditer(nospace(ref_text)):
        t = m.group(0)
        if t not in terms:
            terms.append(t)
    for q in re.findall(r"[“\"]([^”\"]{2,14})[”\"]", ref_text):
        q = q.strip()
        if q and q not in terms:
            terms.append(q)
    return terms


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / float(len(xs))
    return (sum((x - m) ** 2 for x in xs) / float(len(xs))) ** 0.5


def _in_gb2312(ch):
    """汉字是否落在 GB2312 常用字库内（6763 字）。不在＝生僻字。"""
    try:
        ch.encode("gb2312")
        return True
    except UnicodeEncodeError:
        return False


def ai_tone_scan(text):
    """故事类过朱雀专项：AI 味扫描。规则见 story/anti-ai-story.md。"""
    flat = nospace(text)
    paras = [p for p in re.split(r"[\n\r]+", text) if p.strip()]
    tail = tail_zone(text)
    hard, soft = [], []

    hits = [w for w in ANALYSIS_WORDS if w in flat]
    hard.append(("分析词", hits, "换成「就是／说明」这类口语说法"))

    hits = [w for w in CLICHE_IDIOMS if w in flat]
    hard.append(("情绪标签成语", hits, "换成口语直说或动作带出"))

    hits = [name for rx, name in PARALLEL_PATTERNS if rx.search(flat)]
    hard.append(("工整排比", hits, "拆掉工整句式，并列只说一项"))

    dash = flat.count("——") + flat.count("--")
    hard.append(("破折号次数", ["%d 次（上限 1）" % dash] if dash > 1 else [], "破折号换句号"))

    hits = [w for w in ABSTRACT_TAIL if w in tail]
    hard.append(("结尾抽象总结句", hits, "结尾落回事实／动作／提问，不加升华层"))

    bang = flat.count("！") + flat.count("!")
    if bang > 3:
        soft.append("感叹号 %d 个（真人一般不靠感叹号撑情绪，建议 3 个以内）" % bang)

    sents = [s for s in SENT_SPLIT_RE.split(flat) if len(s) >= 2]
    sl = [len(s) for s in sents]
    sd = _std(sl)
    if sl and sd < 8:
        soft.append("句长标准差 %.1f（偏低，句子长度太整齐；建议拆一句或并一句）" % sd)

    pl = [len(nospace(p)) for p in paras]
    pmean = (sum(pl) / float(len(pl))) if pl else 0.0
    if len(pl) >= 3 and pmean:
        ratio = (max(pl) - min(pl)) / pmean
        if ratio < 0.35:
            soft.append("段落长短极差/均值 %.2f（段落太平均，在段数不变的前提下错开长短）" % ratio)

    fill = [w for w in FILLER_WORDS if w in flat]
    if not fill:
        soft.append("填充词 0 处（讲老实话／心想／并不是／时候／里头／老半天 这类要留，是人工信号）")

    vq = len(set(VAGUE_QUANT_RE.findall(flat)))
    if vq < 2:
        soft.append("模糊数量词 %d 处（建议至少 2 处：四十来万／七千出头／三十几岁）" % vq)

    rare = sorted({ch for ch in flat
                   if "\u4e00" <= ch <= "\u9fff" and not _in_gb2312(ch)})
    if rare:
        soft.append("生僻字 %s（超出 GB2312 常用字库；大白话不许用生僻字，换成常用字）"
                    % "".join(rare))

    return {
        "hard": hard,
        "soft": soft,
        "stats": {"破折号": dash, "感叹号": bang, "句长标准差": round(sd, 1),
                  "填充词": len(fill), "模糊量词": vq,
                  "段落极差比": round((max(pl) - min(pl)) / pmean, 2) if (pl and pmean) else 0.0},
    }


def tail_zone(text, n=1):
    paras = [p for p in re.split(r"[\n\r]+", text) if p.strip()]
    return "".join(paras[-n:]) if paras else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("new")
    # 黑名单随罗列式线归到 list/；保留旧路径兜底，避免目录结构回退时静默失效
    _skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _bl = os.path.join(_skill_root, "list", "list-cliche-blacklist.md")
    if not os.path.exists(_bl):
        _bl = os.path.join(_skill_root, "references", "list-cliche-blacklist.md")
    ap.add_argument("--blacklist", default=_bl)
    ap.add_argument("--banned", default="", help="逗号分隔的禁列词（原文高辨识度词）")
    ap.add_argument("--min6", type=int, default=6)
    ap.add_argument("--quiet", action="store_true", help="只输出最后一行结论")
    args = ap.parse_args()

    for p in (args.ref, args.new):
        if not os.path.isfile(p):
            print("找不到文件：%s" % p)
            sys.exit(2)

    ref_raw, new_raw = read_text(args.ref), read_text(args.new)
    ref_plain, new_plain = plain(ref_raw), plain(new_raw)
    fails, warns = [], []
    Q = args.quiet

    def say(*a):
        if not Q:
            print(*a)

    # 文体自动判别
    sr, sn = scan_skeleton(ref_raw), scan_skeleton(new_raw)
    is_list = sr["items"] >= 3
    form = "罗列式" if is_list else "故事型"

    say("=" * 58)
    say("YC-FX-skill 机检 · 判定文体：%s" % form)
    say("参考文：%s    新稿：%s" % (os.path.basename(args.ref), os.path.basename(args.new)))
    say("=" * 58)

    # ① 字数
    rc, nc = count_chars(ref_raw), count_chars(new_raw)
    lo, hi = int(rc * 0.9), int(rc * 1.1)
    ok_len = lo <= nc <= hi
    say("\n【字数】原文 %d ／ 区间 %d—%d ／ 新稿 %d" % (rc, lo, hi, nc))
    if ok_len:
        say("  OK")
    else:
        say("  FAIL  超出区间 %+d 字" % (nc - hi if nc > hi else nc - lo))
        fails.append("字数 %d 不在区间 %d—%d" % (nc, lo, hi))

    # ①b 句长形态（跨文体参考值，不判 FAIL）——句子被"整体压短"是拗口信号：
    # 字数不够时应在句外补话，不该在句子内部删虚词、缩搭配。
    def _median(xs):
        xs = sorted(xs)
        n = len(xs)
        if not n:
            return 0.0
        return float(xs[n // 2]) if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0

    def _sent_lens(t):
        return [len(s) for s in re.split(r"[。！？；!?;]", nospace(t)) if len(s) >= 2]

    rsl, nsl = _sent_lens(ref_raw), _sent_lens(new_raw)
    rm, nm = _median(rsl), _median(nsl)
    r_long = 100.0 * sum(1 for x in rsl if x >= 30) / len(rsl) if rsl else 0.0
    n_long = 100.0 * sum(1 for x in nsl if x >= 30) / len(nsl) if nsl else 0.0
    say("\n【句长形态】原文 中位 %.0f 字（30 字以上占 %.0f%%）／ 新稿 中位 %.0f 字（30 字以上占 %.0f%%）"
        % (rm, r_long, nm, n_long))
    if rm and nm < rm * 0.85:
        drop = 100.0 * (1 - nm / rm)
        say("  提醒  句长中位比原文低 %.0f%%，句子被整体压短——这是拗口信号：字数不够靠\"补话\"、不靠\"压句\"" % drop)
        warns.append("句长中位比原文低 %.0f%%，句子被整体压短（拗口信号）" % drop)
    else:
        say("  OK")

    # ② 重合
    spans = longest_common_spans(ref_plain, new_plain, 4)
    big = sorted([s for s in spans if s[1] >= args.min6], key=lambda x: -x[1])
    small = sorted([s for s in spans if 4 <= s[1] < args.min6], key=lambda x: -x[1])
    say("\n【连续 %d 字以上完全重合】应为 0" % args.min6)
    if big:
        for _, ln, s in big[:12]:
            say("  FAIL  %d字「%s」" % (ln, s))
        fails.append("与原文存在 %d 处 ≥%d 字完全重合" % (len(big), args.min6))
    else:
        say("  OK    0 条")
    if small:
        say("【4—5 字重合】逐条判断是否主题必需词")
        for _, ln, s in small[:10]:
            say("  待判  %d字「%s」" % (ln, s))
        warns.append("%d 处 4—5 字重合，确认是否必需词" % len(small))

    # ③ 禁列词
    banned = [b.strip() for b in re.split(r"[,，]", args.banned) if b.strip()]
    say("\n【禁列词】原文高辨识度内容，一个都不许出现")
    if banned:
        flat = nospace(new_raw)
        bhit = [b for b in banned if b in flat]
        if bhit:
            for b in bhit:
                say("  FAIL  「%s」" % b)
            fails.append("禁列词命中 %d 个：%s" % (len(bhit), "／".join(bhit)))
        else:
            say("  OK    0 命中（检查 %d 个）" % len(banned))
    else:
        risk = ref_risk_terms(ref_raw)
        say("  未传 --banned；原文高危内容供参考：%s" % ("、".join(risk[:14]) or "无"))

    # ④ 套话
    say("\n【套话黑名单】开头位／结尾位命中即 FAIL")
    zones = parse_blacklist(args.blacklist)
    if any(zones.values()):
        hits = check_blacklist(new_raw, zones)
        if hits:
            hard = [h for h in hits if h[0] in ("开头位", "结尾位")]
            for zone, shown in hits:
                say("  %s  %s：%s" % ("FAIL" if zone in ("开头位", "结尾位") else "换掉", zone, shown))
            if hard:
                fails.append("套话在开头位／结尾位命中 %d 处" % len(hard))
        else:
            say("  OK    0 命中")
    else:
        say("  跳过（黑名单不可读）")

    # ⑤ 骨架
    if is_list:
        say("\n【骨架一致性】条数／编号／排版／数量承诺／编号写法")
        # 编号只比"阿拉伯／中文"大类，同一篇内统一即可；"1、"与"1."这类分隔符号差异不算不一致。
        rows = [
            ("条数", sr["items"], sn["items"], sr["items"] == sn["items"]),
            ("编号样式", sr["style"], sn["style"], sr["style"] == sn["style"]),
            ("排版", "%s(%.2f)" % (sr["layout"], sr["per_item"]),
             "%s(%.2f)" % (sn["layout"], sn["per_item"]),
             abs(sr["per_item"] - sn["per_item"]) <= 0.5),
            # 数量承诺只比"数字"，不比量词——规则允许"六句／八条／几点"这类量词改写，
            # 逼着新文沿用原文同一个量词反而是把新文往原文句式上推。
            ("数量承诺", sr["commit"], sn["commit"],
             (sr["commit_sig"] is None) == (sn["commit_sig"] is None)
             and (sr["commit_sig"] is None
                  or sr["commit_sig"][0] == sn["commit_sig"][0])),
        ]
        for name, a, b, ok in rows:
            say("  %s  %-8s 原文：%s   新稿：%s" % ("OK  " if ok else "FAIL", name, a, b))
            if not ok:
                fails.append("骨架不一致：%s（原文 %s，新稿 %s）" % (name, a, b))
        # 编号写法：新稿行首若写成"数字＋半角点／半角右括号＋空白"，markdown 会渲染成
        # 有序列表，编号由渲染器生成、复制时会丢——一律改成中文顿号（1、）。
        if sn["unsafe_sep"]:
            where = "／".join(str(i) for i in sn["unsafe_sep"][:8])
            say("  FAIL  编号写法   第 %s 行用半角“数字.”起行，会被当成有序列表、复制丢编号 → 改中文顿号（1、）" % where)
            fails.append("编号写法不安全：第 %s 行（半角数字点起行，复制会丢编号）" % where)
        else:
            say("  OK    编号写法   行首编号可复制（不触发列表渲染）")
    else:
        say("\n【段落数】原文 %d 段 ／ 新稿 %d 段（允许 ±2）" % (sr["lines"], sn["lines"]))
        if abs(sr["lines"] - sn["lines"]) > 2:
            say("  FAIL  段数超出 ±2")
            fails.append("段数 %d 与原文 %d 相差超过 2" % (sn["lines"], sr["lines"]))
        else:
            say("  OK")

    # ⑥ 人工特征（仅故事类；规则见 story/anti-ai-story.md）
    ai = None
    if is_list:
        say("\n【人工特征】罗列式不做朱雀处理，跳过")
    else:
        ai = ai_tone_scan(new_raw)
        say("\n【人工特征·过朱雀】故事类专项，规则见 anti-ai-story.md")
        for name, hits, fix in ai["hard"]:
            if hits:
                say("  FAIL  %-9s %s  → %s" % (name, "／".join(hits), fix))
                fails.append("人工特征：%s 命中 %s" % (name, "／".join(hits)))
            else:
                say("  OK    %-9s" % name)
        st = ai["stats"]
        say("  参考值  破折号%d ／ 感叹号%d ／ 句长标准差%.1f ／ 填充词%d ／ 模糊量词%d ／ 段落极差比%.2f"
            % (st["破折号"], st["感叹号"], st["句长标准差"], st["填充词"],
               st["模糊量词"], st["段落极差比"]))
        if ai["soft"]:
            say("  提醒（不 FAIL，但真人稿通常不在这些位置）：")
            for w in ai["soft"]:
                say("   ・%s" % w)
            warns.extend(ai["soft"])

    # 结论
    head = "结论：FAIL —— 不许交稿" if fails else "结论：PASS —— 可交稿"
    if not Q:
        print("\n" + "=" * 58)
        print(head)
        for f in fails:
            print("  ・%s" % f)
        if warns:
            print("提醒：")
            for w in warns:
                print("  ・%s" % w)
        print("=" * 58)
    else:
        ai_txt = "跳过(罗列式)" if is_list else (
            "OK" if not any(h for _, h, _ in ai["hard"]) else
            "FAIL %d 项" % len([1 for _, h, _ in ai["hard"] if h]))
        print("机检 %s ｜ %s ｜ 字数 %d/%d(区间%d-%d) ｜ %d字以上重合 %d 处 ｜ 禁列词命中 %d ｜ 套话命中 %d ｜ 人工特征 %s" % (
            "PASS" if not fails else "FAIL", form, nc, rc, lo, hi, args.min6, len(big),
            len([b for b in banned if b in nospace(new_raw)]) if banned else 0,
            len(check_blacklist(new_raw, zones)) if any(zones.values()) else 0,
            ai_txt))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
