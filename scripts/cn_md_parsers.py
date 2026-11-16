# -*- coding: utf-8 -*-
"""东吴证券 AI 研报 markdown 解析器（纯函数，仅用 re/datetime）。

输入两类 md 全文：
- parse_review: 《A股市场情绪复盘》（游资复盘）
- parse_news:   《多资产新闻日览》

纪律：解析不出返回 None / 空列表，禁止编造；入口锚点缺失返回 None。
每个返回 dict 带 parse_coverage = "成功字段数/应抽字段数"。
"""

import re

# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _yi(value_str, wan_str):
    """'1.66'+'万' -> 16600.0 ；'4600'+'' -> 4600.0"""
    v = _to_float(value_str)
    if v is None:
        return None
    return v * 10000.0 if wan_str and "万" in wan_str else v


def _section(md, header_re):
    """取 header_re 匹配的节正文：到下一个 '## ' / '### ' / '---' 或文末。"""
    m = re.search(header_re, md)
    if not m:
        return None
    start = m.end()
    ends = [x.start() for x in re.finditer(r"\n(?:#{2,3} |---|\*\*结论\*\*)", md[start:])]
    end = start + ends[0] if ends else len(md)
    return md[start:end]


# ---------------------------------------------------------------------------
# parse_review
# ---------------------------------------------------------------------------

# 股票名守卫：这些"名字"不是股票
_STOP_NAMES = {
    "最高板", "连板", "首板", "断板", "地天板", "涨停", "跌停",
    "换手板", "T字板", "一字板", "今日", "昨日", "当日", "次日",
    "前一交易日", "机器人", "梯队", "天花板",
}
# 名字末字为这些动词/量词的，几乎必然是把前文动词误当股票名
_BAD_SUFFIX = set("于至升降由进共的开放级批只端家在时兼场现队")
_NAME_CLS = r"[一-龥*][一-龥A-Za-z*]{1,7}?"

_IDX_NAMES = ("上证指数", "深证成指", "创业板指")


def _clean_name(name):
    # 剥离限定前缀："高度端/中低位端/白酒方向/纯谐音的…"（真股票名不含 端/方向/的）
    orig = name
    name = re.sub(r"^[一-龥]{0,4}端", "", name)
    name = re.sub(r"^[一-龥]{0,3}方向", "", name)
    if "的" in name and len(name.rsplit("的", 1)[1]) >= 2:
        name = name.rsplit("的", 1)[1]
    prefixed = name != orig
    # 引导动词剥离；"有"字仅在前缀剥离后（"连板端有桂发祥"）才剥，避免误伤"有研新材"
    name = re.sub(r"^(但|且|而|与|和|或|又|并|是|属|系|为)", "", name)
    if prefixed:
        name = re.sub(r"^有", "", name)
    # 剥离后缀动词
    name = re.sub(r"(涨停|跌停|续板|断板|反包板|晋级板)$", "", name)
    if not name or name in _STOP_NAMES:
        return None
    if name[-1] in _BAD_SUFFIX:
        return None
    return name


# 两种抽高标的模式：A. 名称N(连)板（括号描述…）  B. 名称（描述，N(连)板，…）
_RE_STOCK_DIRECT = re.compile(
    r"(?<![0-9一-龥A-Za-z*])(" + _NAME_CLS + r")(\d{1,2})连?板"
    r"(?:（([^（]{0,120}?)）|\(([^()]{0,120}?)\))?"
)
_RE_STOCK_PAREN = re.compile(
    r"(?<![0-9一-龥A-Za-z*])(" + _NAME_CLS + r")（([^（]{0,120}?)）"
)


def _extract_stock_boards(text):
    """返回 [(name, boards, note)]，note 已截 60 字。"""
    found = []
    for m in _RE_STOCK_DIRECT.finditer(text):
        name = _clean_name(m.group(1))
        if name is None:
            continue
        boards = int(m.group(2))
        if not (2 <= boards <= 15):
            continue
        desc = m.group(3) or m.group(4) or ""
        note = ("%d板（%s）" % (boards, desc))[:60] if desc else "%d板" % boards
        found.append((name, boards, note))
    for m in _RE_STOCK_PAREN.finditer(text):
        name = _clean_name(m.group(1))
        if name is None:
            continue
        inner = m.group(2)
        # "N天M板"为累计口径（非当前连板），"前一交易日"为前值——均不可当梯队高度
        if "前一交易日" in inner or re.search(r"\d+\s*天\s*\d+\s*板", inner):
            continue
        bm = re.search(r"(\d{1,2})连?板", inner)
        if not bm:
            continue
        boards = int(bm.group(1))
        if not (2 <= boards <= 15):
            continue
        found.append((name, boards, ("（%s）" % inner)[:60]))
    # 集体句式："正裕工业（机器人零部件）、…五只晋级3板，万向钱潮、…六只晋级2板"
    for m in re.finditer(r"[一两二三四五六七八九十\d]+只晋级(\d{1,2})连?板", text):
        boards = int(m.group(1))
        if not (2 <= boards <= 15):
            continue
        sent_start = max(text.rfind("。", 0, m.start()), text.rfind("；", 0, m.start()),
                         text.rfind("\n", 0, m.start())) + 1
        seg = text[max(sent_start, m.start() - 200): m.start()]
        for piece in seg.split("、"):
            piece = piece.strip("——-， ")
            pm = re.match(r"^[^（()]*?([一-龥*][一-龥A-Za-z*]{1,6})（([^（）]*)）[^（）]*$", piece)
            if pm:
                name = _clean_name(pm.group(1))
                if name:
                    found.append((name, boards, ("（%s）" % pm.group(2))[:60]))
                continue
            if re.match(r"^[一-龥*]{2,6}$", piece):
                name = _clean_name(piece)
                if name:
                    found.append((name, boards, "%d板" % boards))
    return found


def _merge_ladder(items):
    """items: [(name, boards, note, theme)]；同股票取最高板，按板数降序。"""
    best = {}
    order = {}
    for i, (name, boards, note, theme) in enumerate(items):
        cur = best.get(name)
        if cur is None or boards > cur[0]:
            best[name] = (boards, note, theme)
            order[name] = i
    ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], order[kv[0]]))
    return [
        {"stock": n, "boards": b, "theme": theme, "note": note}
        for n, (b, note, theme) in ranked
    ]


def _parse_money_effect(md):
    """赚钱/亏钱效应百分数；'前一交易日'括号内前值要跳过，'今日X%'优先。"""
    for m in re.finditer(r"(?:亏钱|赚钱)效应", md):
        win = md[m.end(): m.end() + 45]
        if "今日" in win:
            tm = re.search(r"今日\s*([+\-]?\d+(?:\.\d+)?)%", win)
            if tm:
                return float(tm.group(1))
        for pm in re.finditer(r"([+\-]?\d+(?:\.\d+)?)%", win):
            pre = win[max(0, pm.start() - 6): pm.start()]
            if "前一交易日" in pre or "前值" in pre:
                continue
            return float(pm.group(1))
    return None


def _parse_regulatory(md):
    sec = _section(md, r"##\s*五、[^\n]*")
    if not sec:
        return []
    out = []
    for m in re.finditer(
        r"(严重异动|临触发|多次异动|重点监控)\s*(\d+)只(?:——([^\n。]+))?", sec
    ):
        cnt = int(m.group(2))
        body = m.group(3)
        if cnt == 0 or not body:
            continue
        for em in re.finditer(
            r"(" + _NAME_CLS + r")（([^（]{0,80}?)）", body
        ):
            name = _clean_name(em.group(1))
            if name is None:
                continue
            out.append({
                "type": m.group(1),
                "stock": name,
                "detail": em.group(2)[:50],
            })
    return out


def _parse_lhb(md):
    """机构净买/净卖。显式标记："名称机构净买(入)N亿元"；省略式并列：
    "机构表态N只——A机构净买入3.67亿元、B 2.06亿元、C 1.99亿元"（链式继承，仅限该句内）。
    """
    sec = _section(md, r"##\s*四、[^\n]*")
    if not sec:
        return [], []
    seg = re.search(r"机构表态\s*\d+\s*只——([^\n。]+)", sec)
    text = seg.group(1) if seg else sec
    chain = bool(seg)  # 省略式并列仅在"机构表态"句内继承
    buys, sells = [], []
    last_side = None

    def add(side, name, amount):
        name = _clean_name(name)
        tgt = buys if side == "buy" else sells
        if name and len(name) <= 6 and name not in [x["stock"] for x in tgt]:
            tgt.append({"stock": name, "amount_yi": amount})

    for piece in re.split(r"[、；]", text):
        m = re.match(r"^(" + _NAME_CLS + r")机构净买(?:入)?\s*([\d.]+)\s*亿元", piece)
        if m:
            add("buy", m.group(1), float(m.group(2)))
            last_side = "buy"
            continue
        m = re.match(r"^(" + _NAME_CLS + r")机构净卖(?:出|售)?\s*([\d.]+)\s*亿元", piece)
        if m:
            add("sell", m.group(1), float(m.group(2)))
            last_side = "sell"
            continue
        if chain and last_side:
            m = re.match(r"^(" + _NAME_CLS + r")\s*([\d.]+)\s*亿元", piece)
            if m:
                add(last_side, m.group(1), float(m.group(2)))
                continue
        last_side = None
    return buys[:8], sells[:8]


def parse_review(md):
    """解析《A股市场情绪复盘》md 全文。锚点=核心判断；缺锚点/空文返回 None。"""
    if not md or not isinstance(md, str) or "核心判断" not in md:
        return None

    out = {}

    # 1. review_date
    dm = re.search(r"#\s*A股市场情绪复盘\s*(\d{4})年(\d{1,2})月(\d{1,2})日", md)
    out["review_date"] = "%04d-%02d-%02d" % tuple(int(g) for g in dm.groups()) if dm else None

    # 2. core_judgement
    cj = re.search(r"\*\*核心判断：(.+?)\*\*", md, re.S)
    out["core_judgement"] = cj.group(1).strip() if cj else None

    # 3/4. limit_up / limit_down（取当日值，非括号内前值）
    lu = re.search(r"涨停(\d+)只（前一交易日\d+只）", md) or re.search(r"涨停(\d+)只", md)
    ld = re.search(r"跌停(\d+)只（前一交易日\d+只）", md) or re.search(r"跌停(\d+)只", md)
    out["limit_up"] = int(lu.group(1)) if lu else None
    out["limit_down"] = int(ld.group(1)) if ld else None

    # 5. money_effect
    out["money_effect"] = _parse_money_effect(md)

    # 6. max_boards（"最高板(反而)由4板升至5板"/"最高板5板"/"最高板升至4板"）
    mb = re.search(r"最高板(?:反而)?由?(?:(\d+)板)?[升降回落]*至?(\d+)板", md)
    out["max_boards"] = int(mb.group(2)) if mb else None

    # 7. total_amount_yi：三种写法统一
    ta = re.search(
        r"全A成交额(?:(?:放量|缩量)约?([\d.]+)(万?)亿元至)?([\d.]+)(万?)亿元", md
    )
    out["total_amount_yi"] = _yi(ta.group(3), ta.group(4)) if ta else None

    # 8. amount_delta_yi
    ad = re.search(r"(缩量|放量)约?([\d.]+)(万?)亿元", md)
    if ad:
        v = _yi(ad.group(2), ad.group(3))
        out["amount_delta_yi"] = -v if ad.group(1) == "缩量" else v
    else:
        out["amount_delta_yi"] = None

    # 9. index_moves
    moves = {}
    for im in re.finditer(r"(%s)全天([+\-]?[\d.]+)%%" % "|".join(_IDX_NAMES), md):
        moves.setdefault(im.group(1), float(im.group(2)))
    out["index_moves"] = moves or None

    # 10. ladder：观察线各节为主，抽空再从"二、情绪与主线"正文补
    items = []
    heads = list(re.finditer(
        r"###\s*观察线[一二三四五六七八九十]+\s*[：:]\s*([^\n（(]+)", md))
    for i, h in enumerate(heads):
        theme = h.group(1).strip()
        seg_end = heads[i + 1].start() if i + 1 < len(heads) else None
        body = md[h.end(): seg_end] if seg_end else md[h.end():]
        body = re.split(r"\n## ", body)[0]
        for name, boards, note in _extract_stock_boards(body):
            items.append((name, boards, note, theme))
    if not items:
        sec2 = _section(md, r"##\s*二、[^\n]*")
        if sec2:
            for name, boards, note in _extract_stock_boards(sec2):
                items.append((name, boards, note, None))
    ladder = _merge_ladder(items)
    # 文档自证围栏：梯队高度不可能超过当日"最高板"
    if out["max_boards"] is not None:
        ladder = [x for x in ladder if x["boards"] <= out["max_boards"]]
    out["ladder"] = ladder

    # 11. lhb_inst_buys / lhb_inst_sells
    buys, sells = _parse_lhb(md)
    out["lhb_inst_buys"] = buys
    out["lhb_inst_sells"] = sells

    # 12. regulatory
    out["regulatory"] = _parse_regulatory(md)

    # 13. watch_next
    wn = []
    sec7 = _section(md, r"##\s*七、[^\n]*")
    if sec7:
        for wm in re.finditer(r"\d+\.\s*\*\*(.+?)\*\*[：:]\s*([^\n]+)", sec7):
            first = re.split(r"。", wm.group(2))[0]
            first = first + "。" if "。" in wm.group(2) else first
            wn.append(("%s：%s" % (wm.group(1), first))[:80])
    out["watch_next"] = wn

    fields = ["review_date", "core_judgement", "limit_up", "limit_down",
              "money_effect", "max_boards", "total_amount_yi", "amount_delta_yi",
              "index_moves", "ladder", "lhb_inst_buys", "lhb_inst_sells",
              "regulatory", "watch_next"]
    ok = sum(1 for f in fields if out[f] not in (None, [], {}))
    out["parse_coverage"] = "%d/%d" % (ok, len(fields))
    return out


# ---------------------------------------------------------------------------
# parse_news
# ---------------------------------------------------------------------------

def parse_news(md):
    """解析《多资产新闻日览》md 全文。锚点=执行摘要节；缺锚点/空文返回 None。"""
    if not md or not isinstance(md, str) or "## 📌 执行摘要" not in md:
        return None

    out = {}

    # 1. news_date
    dm = re.search(r"#\s*多资产新闻日览\s*(\d{4}-\d{2}-\d{2})", md)
    out["news_date"] = dm.group(1) if dm else None

    # 2. catalysts：① **标题**——正文…【tag】【涉及】…
    cat = []
    sec = _section(md, r"##\s*🎯\s*当日核心催化[^\n]*")
    if sec:
        for cm in re.finditer(
            r"[①②③④⑤]\s*\*\*(.+?)\*\*\s*——([^\n]+)", sec
        ):
            body = cm.group(2)
            tagm = re.search(r"【([^】]{1,6})】", body)
            cat.append({
                "title": cm.group(1).strip(),
                "tag": tagm.group(1) if tagm else None,
                "body": body[:120],
            })
    out["catalysts"] = cat

    # 3. asset_panorama：markdown 表
    pano = []
    sec = _section(md, r"##\s*📊\s*大类资产全景图[^\n]*")
    if sec:
        for row in re.finditer(r"^\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*$",
                               sec, re.M):
            cells = [c.strip() for c in row.groups()]
            if set(cells[0]) <= set("-: ") or cells[0] in ("资产维度",):
                continue
            dim = re.sub(r"（\s*\d+\s*条\s*）$", "", cells[0]).strip()
            pano.append({
                "dimension": dim,
                "events": cells[1][:80],
                "sentiment": cells[2],
                "signal": cells[3],
            })
    out["asset_panorama"] = pano

    # 4. watchlist
    wl = []
    sec = _section(md, r"##\s*⏳\s*待验证清单[^\n]*")
    if sec:
        for wm in re.finditer(r"^\d+\.\s*(\*\*.+?\*\*[^\n]*)$", sec, re.M):
            wl.append(wm.group(1).strip()[:100])
    out["watchlist"] = wl

    # 5. exec_summary_paras：粗体开头段落的 粗体部分+紧跟首句
    paras = []
    sec = _section(md, r"##\s*📌\s*执行摘要[^\n]*")
    if sec:
        for pm in re.finditer(r"^\*\*(.+?)\*\*\s*([^\n]*)$", sec, re.M):
            lead = pm.group(1).strip()
            rest = pm.group(2).strip()
            first = rest.split("。")[0] + ("。" if "。" in rest else "")
            joiner = "" if lead.endswith("。") else "："
            paras.append(("%s%s%s" % (lead, joiner, first))[:120])
    out["exec_summary_paras"] = paras[:5]

    fields = ["news_date", "catalysts", "asset_panorama", "watchlist",
              "exec_summary_paras"]
    ok = sum(1 for f in fields if out[f] not in (None, [], {}))
    out["parse_coverage"] = "%d/%d" % (ok, len(fields))
    return out
