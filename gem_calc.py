#!/usr/bin/env python3
"""마지막이야기 보석 계산기 CLI."""

import argparse
import itertools
import json
import re
import sys
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / "state.json"

GEM_RE = re.compile(r"(공|단|속)((?:\d+/)*\d+)")
OPTIONS = ("공", "단", "속")


# ---------------------------------------------------------------------------
# 상태 로드/저장
# ---------------------------------------------------------------------------

def load_state():
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# 보석 문자열 파싱
# ---------------------------------------------------------------------------

def parse_gem_slots(gem_str):
    """보석 문자열을 (옵션, 값) 슬롯 리스트로 분해한다. '속4/5/4'는 속 슬롯 3개로 취급."""
    slots = []
    for m in GEM_RE.finditer(gem_str):
        letter = m.group(1)
        for n in m.group(2).split("/"):
            slots.append((letter, int(n)))
    return slots


def parse_gem(gem_str):
    """같은 옵션끼리 합산한 {'공':x, '단':y, '속':z} 딕셔너리를 반환."""
    opts = {"공": 0, "단": 0, "속": 0}
    for letter, val in parse_gem_slots(gem_str):
        opts[letter] += val
    return opts


def validate_gem_str(gem_str, option_ranges):
    """단일 슬롯 값이 옵션 단독 최댓값을 초과하면 경고만 출력한다."""
    for letter, val in parse_gem_slots(gem_str):
        _, hi = option_ranges.get(letter, (None, None))
        if hi is not None and val > hi:
            print(
                f"[경고] '{gem_str}' 의 {letter}{val} 는 옵션 단독 최댓값({letter}{hi})을 "
                f"초과합니다 (합산 표기로 간주하고 계속 진행합니다).",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# 핵심 계산 함수
# ---------------------------------------------------------------------------

def atk_eff(x, total_gear):
    if total_gear == 0:
        return 0.0
    return ((total_gear + x) ** 2 - total_gear ** 2) / total_gear ** 2 * 100


def linear_eff(x, total_base):
    if total_base == 0:
        return 0.0
    return x / total_base * 100


def total_from_state(state):
    """equipped 전체의 공/단/속 보석 합계를 base의 '제외' 값과 더해 '포함' 총 기저값을 계산."""
    base = state["base"]
    sums = {"공": 0, "단": 0, "속": 0}
    for gems in state["equipped"].values():
        for g in gems:
            opts = parse_gem(g)
            for k in sums:
                sums[k] += opts[k]
    return {
        "total_gear": base["gear_no_gem"] + sums["공"],
        "total_dan": base["dan_no_gem"] + sums["단"],
        "total_spd_guild": base["spd_no_gem_guild"] + sums["속"],
        "total_spd_world": base["spd_no_gem_world"] + sums["속"],
        "gem_sums": sums,
    }


def gem_eff(gem_str, totals):
    """보석 문자열의 길드/월드 종합 효율(%)을 (guild, world) 튜플로 반환."""
    opts = parse_gem(gem_str)
    a = atk_eff(opts["공"], totals["total_gear"])
    d = linear_eff(opts["단"], totals["total_dan"])
    s_guild = linear_eff(opts["속"], totals["total_spd_guild"])
    s_world = linear_eff(opts["속"], totals["total_spd_world"])
    return a + d + s_guild, a + d + s_world


# ---------------------------------------------------------------------------
# 랭킹 / 기준보석
# ---------------------------------------------------------------------------

def flatten_equipped(state):
    items = []
    for part, gems in state["equipped"].items():
        for idx, g in enumerate(gems):
            items.append({"part": part, "idx": idx, "gem": g})
    return items


def rank_list(gem_strs, totals):
    """(gem_str, guild, world) 튜플 리스트를 guild 내림차순으로 정렬해 반환."""
    ranked = [(g, *gem_eff(g, totals)) for g in gem_strs]
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


def find_baseline(state, totals):
    """장착 중 최저효율(guild 기준) 보석을 반환: (part, idx, gem, guild, world)."""
    items = flatten_equipped(state)
    best = None
    for it in items:
        guild, world = gem_eff(it["gem"], totals)
        if best is None or guild < best[3]:
            best = (it["part"], it["idx"], it["gem"], guild, world)
    return best


def rank_position(value, other_values):
    """value를 other_values 사이에 내림차순으로 끼워 넣었을 때의 순위(1-base)."""
    higher = sum(1 for v in other_values if v > value)
    return higher + 1


# ---------------------------------------------------------------------------
# 리롤 가치 판정
# ---------------------------------------------------------------------------

def premium_multiplier(price, gain_pct, state):
    if gain_pct is None or gain_pct <= 0:
        return None
    return (price / gain_pct) / state["ref_price_per_pct"]


def reroll_verdict_text(gain, state):
    if gain <= 0:
        return "리롤 불필요, 이미 평균 이상", None
    premium = premium_multiplier(state["reroll_ticket_price"], gain, state)
    if premium < 10:
        return "사서 리롤 돌려볼만 합니다!", premium
    return "이득 볼 확률이 있긴 하지만 비효율적입니다!", premium


def combo_profile(type_str):
    from collections import Counter

    return Counter(type_str)


def gem_type_profile(gem_str):
    from collections import Counter

    return Counter(letter for letter, _ in parse_gem_slots(gem_str))


def reroll_avg_for_combo(types, totals, option_ranges):
    """types: ['공','속','속'] 같은 슬롯 리스트. 평균/최댓값 (guild, world) 반환."""
    ranges = [range(option_ranges[t][0], option_ranges[t][1] + 1) for t in types]

    count = 0
    sum_guild = 0.0
    sum_world = 0.0
    max_guild = None
    max_world = None

    for combo in itertools.product(*ranges):
        sums = {"공": 0, "단": 0, "속": 0}
        for letter, val in zip(types, combo):
            sums[letter] += val
        a = atk_eff(sums["공"], totals["total_gear"])
        d = linear_eff(sums["단"], totals["total_dan"])
        s_guild = linear_eff(sums["속"], totals["total_spd_guild"])
        s_world = linear_eff(sums["속"], totals["total_spd_world"])
        guild = a + d + s_guild
        world = a + d + s_world

        count += 1
        sum_guild += guild
        sum_world += world
        if max_guild is None or guild > max_guild:
            max_guild = guild
        if max_world is None or world > max_world:
            max_world = world

    return sum_guild / count, sum_world / count, max_guild, max_world


# ---------------------------------------------------------------------------
# 출력 헬퍼
# ---------------------------------------------------------------------------

def pct(x):
    return f"{x:.2f}%"


def mult(x):
    return f"{x:.2f}배"


# ---------------------------------------------------------------------------
# 커맨드 구현
# ---------------------------------------------------------------------------

def cmd_eval(args, state):
    validate_gem_str(args.gem, state["option_ranges"])
    totals = total_from_state(state)
    guild, world = gem_eff(args.gem, totals)

    base_part, base_idx, base_gem, base_guild, base_world = find_baseline(state, totals)

    if guild > base_guild:
        cmp_word = "높"
    elif guild < base_guild:
        cmp_word = "낮"
    else:
        cmp_word = "같"

    equipped_gems = [it["gem"] for it in flatten_equipped(state)]
    equipped_values = [gem_eff(g, totals)[0] for g in equipped_gems]
    inventory_values = [gem_eff(g, totals)[0] for g in state["inventory"]]

    rank_equip = rank_position(guild, equipped_values)
    rank_combined = rank_position(guild, equipped_values + inventory_values)

    print("현재 세팅에서")
    print(f"{args.gem}은 ({pct(guild)}/길드, {pct(world)}/월드) 상승하고,")
    print(f"{base_gem}({base_part})({pct(base_guild)})보다 효율이 {cmp_word}습니다.")
    print(f"장착 순위: {rank_equip}위 / 보유+장착 통합 순위: {rank_combined}위")

    if args.price is not None:
        gain = guild - base_guild
        premium = premium_multiplier(args.price, gain, state)
        if premium is None:
            print("프리미엄 계산 불가 (기준보석 대비 상승폭 없음)")
        else:
            print(f"프리미엄 {mult(premium)}")

    types = [letter for letter, _ in parse_gem_slots(args.gem)]
    if types:
        avg_guild, _avg_world, _max_g, _max_w = reroll_avg_for_combo(
            types, totals, state["option_ranges"]
        )
        gain = avg_guild - guild
        verdict, _ = reroll_verdict_text(gain, state)
        print(verdict)


def cmd_equip(args, state):
    part = args.part
    if part not in state["equipped"]:
        print(f"[오류] '{part}' 부위가 존재하지 않습니다. 가능한 부위: {list(state['equipped'])}")
        sys.exit(1)
    gems = state["equipped"][part]
    if not (0 <= args.index < len(gems)):
        print(f"[오류] '{part}' 부위의 인덱스는 0~{len(gems) - 1} 범위여야 합니다.")
        sys.exit(1)

    validate_gem_str(args.gem, state["option_ranges"])

    old_gem = gems[args.index]
    gems[args.index] = args.gem

    if args.gem in state["inventory"]:
        state["inventory"].remove(args.gem)
    state["inventory"].append(old_gem)

    save_state(state)

    totals = total_from_state(state)

    print("=== 장착 현황 ===")
    for p, gs in state["equipped"].items():
        print(f"[{p}] {', '.join(gs)}")
    print()
    print("=== 새 총 기저값 ===")
    print(f"장비공: {pct(totals['total_gear'])}")
    print(f"단일: {pct(totals['total_dan'])}")
    print(f"속퍼(길드): {pct(totals['total_spd_guild'])}")
    print(f"속퍼(월드): {pct(totals['total_spd_world'])}")
    print()

    print("=== 장착 보석 전체 순위 ===")
    equipped_items = flatten_equipped(state)
    ranked = sorted(
        ((it, *gem_eff(it["gem"], totals)) for it in equipped_items),
        key=lambda t: t[1],
        reverse=True,
    )
    for i, (it, guild, world) in enumerate(ranked, start=1):
        print(f"{i}위 [{it['part']}] {it['gem']} - 길드 {pct(guild)} / 월드 {pct(world)}")
    print()

    base_part, base_idx, base_gem, base_guild, base_world = find_baseline(state, totals)
    print(f"=== 기준보석({base_gem}, {base_part}, {pct(base_guild)})보다 효율 높은 인벤토리 보석 ===")
    better = []
    for g in state["inventory"]:
        guild, world = gem_eff(g, totals)
        if guild > base_guild:
            better.append((g, guild, world))
    better.sort(key=lambda t: t[1], reverse=True)
    if not better:
        print("(없음)")
    else:
        for g, guild, world in better:
            print(f"{g} - 길드 {pct(guild)} / 월드 {pct(world)}")


def cmd_rank(args, state):
    totals = total_from_state(state)

    if args.target == "equipped":
        items = flatten_equipped(state)
        ranked = sorted(
            ((it, *gem_eff(it["gem"], totals)) for it in items),
            key=lambda t: t[1],
            reverse=True,
        )
        print("=== 장착 보석 순위 ===")
        for i, (it, guild, world) in enumerate(ranked, start=1):
            print(f"{i}위 [{it['part']}] {it['gem']} - 길드 {pct(guild)} / 월드 {pct(world)}")
    else:
        ranked = rank_list(state["inventory"], totals)
        print("=== 인벤토리 보석 순위 ===")
        for i, (g, guild, world) in enumerate(ranked, start=1):
            print(f"{i}위 {g} - 길드 {pct(guild)} / 월드 {pct(world)}")


def cmd_add(args, state):
    validate_gem_str(args.gem, state["option_ranges"])
    state["inventory"].append(args.gem)
    save_state(state)

    totals = total_from_state(state)
    guild, world = gem_eff(args.gem, totals)

    ranked = rank_list(state["inventory"], totals)
    position = next(i for i, (g, *_r) in enumerate(ranked, start=1) if g == args.gem)

    base_part, base_idx, base_gem, base_guild, base_world = find_baseline(state, totals)
    cmp_word = "높" if guild > base_guild else ("낮" if guild < base_guild else "같")

    print(f"{args.gem} 인벤토리에 추가되었습니다. (길드 {pct(guild)} / 월드 {pct(world)})")
    print(f"인벤토리 내 순위: {position}위 / 총 {len(state['inventory'])}개")
    print(f"기준보석({base_gem}, {base_part})({pct(base_guild)})보다 효율이 {cmp_word}습니다.")


def cmd_reroll_avg(args, state):
    types = list(args.combo)
    for t in types:
        if t not in OPTIONS:
            print(f"[오류] 알 수 없는 옵션 문자 '{t}'. 사용 가능: {OPTIONS}")
            sys.exit(1)

    totals = total_from_state(state)
    avg_guild, avg_world, max_guild, max_world = reroll_avg_for_combo(
        types, totals, state["option_ranges"]
    )

    print(f"=== '{args.combo}' 조합 리롤 통계 ===")
    print(f"평균 효율: 길드 {pct(avg_guild)} / 월드 {pct(avg_world)}")
    print(f"최댓값 효율: 길드 {pct(max_guild)} / 월드 {pct(max_world)}")

    profile = combo_profile(args.combo)
    matches = []
    for it in flatten_equipped(state):
        if gem_type_profile(it["gem"]) == profile:
            matches.append((f"[장착:{it['part']}]", it["gem"]))
    for g in state["inventory"]:
        if gem_type_profile(g) == profile:
            matches.append(("[인벤토리]", g))

    if not matches:
        print("(현재 보유/장착 중 동일 조합 보석 없음)")
        return

    print("=== 보유 중인 동일 조합 보석 리롤 가치 판정 ===")
    for label, g in matches:
        guild, world = gem_eff(g, totals)
        gain = avg_guild - guild
        verdict, premium = reroll_verdict_text(gain, state)
        premium_str = f", 프리미엄 {mult(premium)}" if premium is not None else ""
        print(
            f"{label} {g} (현재 길드 {pct(guild)}) → 평균 대비 {gain:+.2f}%p{premium_str} — {verdict}"
        )


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(description="마지막이야기 보석 계산기")
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("eval", help="보석 효율 평가")
    p_eval.add_argument("gem")
    p_eval.add_argument("price", nargs="?", type=float, default=None)
    p_eval.set_defaults(func=cmd_eval)

    p_equip = sub.add_parser("equip", help="보석 장착 교체")
    p_equip.add_argument("part")
    p_equip.add_argument("index", type=int)
    p_equip.add_argument("gem")
    p_equip.set_defaults(func=cmd_equip)

    p_rank = sub.add_parser("rank", help="순위 출력")
    p_rank.add_argument("target", choices=["equipped", "inventory"])
    p_rank.set_defaults(func=cmd_rank)

    p_add = sub.add_parser("add", help="인벤토리에 보석 추가")
    p_add.add_argument("gem")
    p_add.set_defaults(func=cmd_add)

    p_reroll = sub.add_parser("reroll_avg", help="옵션 조합의 리롤 평균/최댓값 및 가치 판정")
    p_reroll.add_argument("combo")
    p_reroll.set_defaults(func=cmd_reroll_avg)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    state = load_state()
    args.func(args, state)


if __name__ == "__main__":
    main()
