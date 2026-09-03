#!/usr/bin/env python3
"""마지막이야기 보석 계산기 CLI.

state.json 을 읽고 쓰며 보석 효율/리롤 가치를 계산한다.
"""
import argparse
import itertools
import json
import re
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / "state.json"

GEM_RE = re.compile(r"(공|단|속)(\d+)")
OPTIONS = ("공", "단", "속")


# ---------------------------------------------------------------------------
# state.json 입출력
# ---------------------------------------------------------------------------

def load_state(path=STATE_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(state, path=STATE_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# 파싱
# ---------------------------------------------------------------------------

def parse_gem(gem_str):
    """보석 문자열을 옵션별 합계로 파싱한다. 같은 옵션은 합산된다."""
    sums = {opt: 0 for opt in OPTIONS}
    for opt, num in GEM_RE.findall(gem_str):
        sums[opt] += int(num)
    return sums


def parse_types(gem_str):
    """보석 문자열에 등장하는 옵션 종류를 순서대로(중복 포함) 반환한다."""
    return [opt for opt, _ in GEM_RE.findall(gem_str)]


# ---------------------------------------------------------------------------
# 효율 공식
# ---------------------------------------------------------------------------

def atk_eff(x, total_gear):
    return ((total_gear + x) ** 2 - total_gear ** 2) / total_gear ** 2 * 100


def linear_eff(x, total_base):
    return x / total_base * 100


def eff_from_sums(sums, totals):
    """옵션별 합계(sums)와 현재 총 기저값(totals)으로 길드/월드 효율을 구한다."""
    guild = 0.0
    world = 0.0
    if sums["공"]:
        v = atk_eff(sums["공"], totals["gear"])
        guild += v
        world += v
    if sums["단"]:
        v = linear_eff(sums["단"], totals["dan"])
        guild += v
        world += v
    if sums["속"]:
        guild += linear_eff(sums["속"], totals["spd_guild"])
        world += linear_eff(sums["속"], totals["spd_world"])
    return guild, world


def gem_eff(gem_str, totals):
    guild, world = eff_from_sums(parse_gem(gem_str), totals)
    return round(guild, 2), round(world, 2)


def total_from_state(state):
    """equipped 전체의 옵션 합계 + base의 '제외' 값으로 '포함' 총 기저값을 구한다."""
    sums = {opt: 0 for opt in OPTIONS}
    for gems in state["equipped"].values():
        for g in gems:
            s = parse_gem(g)
            for opt in OPTIONS:
                sums[opt] += s[opt]
    base = state["base"]
    return {
        "gear": base["gear_no_gem"] + sums["공"],
        "dan": base["dan_no_gem"] + sums["단"],
        "spd_guild": base["spd_no_gem_guild"] + sums["속"],
        "spd_world": base["spd_no_gem_world"] + sums["속"],
    }


# ---------------------------------------------------------------------------
# 순위
# ---------------------------------------------------------------------------

def rank_equipped(state, totals):
    items = []
    for slot, gems in state["equipped"].items():
        for idx, g in enumerate(gems, start=1):
            guild, world = gem_eff(g, totals)
            items.append({"slot": slot, "idx": idx, "gem": g, "guild": guild, "world": world})
    items.sort(key=lambda it: it["guild"], reverse=True)
    return items


def rank_inventory(state, totals):
    items = []
    for g in state["inventory"]:
        guild, world = gem_eff(g, totals)
        items.append({"gem": g, "guild": guild, "world": world})
    items.sort(key=lambda it: it["guild"], reverse=True)
    return items


def baseline_gem(equipped_ranked):
    """장착 중 최저효율(기준보석)을 동적으로 구한다."""
    return equipped_ranked[-1]


# ---------------------------------------------------------------------------
# 리롤 가치
# ---------------------------------------------------------------------------

def premium(price, gain_pct, ref_price_per_pct):
    if gain_pct is None or gain_pct <= 0:
        return None
    return price / (gain_pct * ref_price_per_pct)


def reroll_combo_stats(combo_types, totals, option_ranges):
    """옵션 조합(예: ['공','속','속'])의 전체 랜덤 결과에 대한 평균/최댓값 효율(길드 기준)."""
    value_ranges = [range(option_ranges[t][0], option_ranges[t][1] + 1) for t in combo_types]
    guild_effs = []
    for combo_values in itertools.product(*value_ranges):
        sums = {opt: 0 for opt in OPTIONS}
        for t, v in zip(combo_types, combo_values):
            sums[t] += v
        guild, _world = eff_from_sums(sums, totals)
        guild_effs.append(guild)
    return sum(guild_effs) / len(guild_effs), max(guild_effs)


def reroll_verdict(current_guild, avg_guild, state):
    gain = avg_guild - current_guild
    if gain <= 0:
        return "리롤 불필요, 이미 평균 이상", None
    prem = premium(state["reroll_ticket_price"], gain, state["ref_price_per_pct"])
    if prem < 10:
        return "사서 리롤 돌려볼만 합니다!", prem
    return "이득 볼 확률이 있긴 하지만 비효율적입니다!", prem


def combo_to_types(combo_str):
    return [ch for ch in combo_str if ch in OPTIONS]


# ---------------------------------------------------------------------------
# 출력 포맷
# ---------------------------------------------------------------------------

def _cmp_word(a, b):
    if a > b:
        return "높습니다"
    if a < b:
        return "낮습니다"
    return "같습니다"


def print_single_eval(gem_str, state, price=None):
    totals = total_from_state(state)
    guild, world = gem_eff(gem_str, totals)

    eq_ranked = rank_equipped(state, totals)
    base = baseline_gem(eq_ranked)
    inv_ranked = rank_inventory(state, totals)

    candidate = {"gem": gem_str, "guild": guild, "world": world}
    eq_with_candidate = sorted(eq_ranked + [candidate], key=lambda it: it["guild"], reverse=True)
    eq_rank_pos = eq_with_candidate.index(candidate) + 1

    all_with_candidate = sorted(eq_ranked + inv_ranked + [candidate], key=lambda it: it["guild"], reverse=True)
    all_rank_pos = all_with_candidate.index(candidate) + 1

    print("현재 세팅에서")
    print(f"{gem_str}은 ({guild:.2f}%/길드, {world:.2f}%/월드) 상승하고,")
    print(f"{base['gem']}({base['guild']:.2f}%)보다 효율이 {_cmp_word(guild, base['guild'])}.")
    print(f"장착 순위: {eq_rank_pos}위 / 보유+장착 통합 순위: {all_rank_pos}위")

    if price is not None:
        prem = premium(price, guild, state["ref_price_per_pct"])
        if prem is not None:
            print(f"프리미엄 {prem:.2f}배")

    combo_types = parse_types(gem_str)
    avg, _mx = reroll_combo_stats(combo_types, totals, state["option_ranges"])
    verdict, _prem = reroll_verdict(guild, avg, state)
    print(verdict)


# ---------------------------------------------------------------------------
# 커맨드
# ---------------------------------------------------------------------------

def cmd_eval(args, state):
    print_single_eval(args.gem, state, args.price)


def cmd_equip(args, state):
    slot = args.slot
    if slot not in state["equipped"]:
        raise SystemExit(f"알 수 없는 부위: {slot} (가능한 부위: {', '.join(state['equipped'])})")
    gems = state["equipped"][slot]
    idx = args.index - 1
    if not (0 <= idx < len(gems)):
        raise SystemExit(f"인덱스 범위 오류: {slot}에는 {len(gems)}개의 보석만 있습니다.")

    old_gem = gems[idx]
    gems[idx] = args.new_gem
    if args.new_gem in state["inventory"]:
        state["inventory"].remove(args.new_gem)
    state["inventory"].append(old_gem)
    save_state(state)

    totals = total_from_state(state)

    print(f"[장착 현황] {slot} {args.index}번 슬롯: {old_gem} → {args.new_gem}")
    print(
        f"새 총 기저값: 공격력 {totals['gear']:.2f}%, 단일 {totals['dan']:.2f}%, "
        f"속퍼 {totals['spd_guild']:.2f}%(길드)/{totals['spd_world']:.2f}%(월드)"
    )
    print()

    print("[장착 보석 전체 순위]")
    eq_ranked = rank_equipped(state, totals)
    for i, it in enumerate(eq_ranked, 1):
        print(f"{i}위 [{it['slot']}] {it['gem']}: {it['guild']:.2f}%/길드, {it['world']:.2f}%/월드")
    print()

    base = baseline_gem(eq_ranked)
    print(f"[기준보석 {base['gem']}({base['guild']:.2f}%)보다 효율 높은 인벤토리 보석]")
    higher = [it for it in rank_inventory(state, totals) if it["guild"] > base["guild"]]
    if higher:
        for it in higher:
            print(f"{it['gem']}: {it['guild']:.2f}%/길드, {it['world']:.2f}%/월드")
    else:
        print("없음")


def cmd_rank(args, state):
    totals = total_from_state(state)
    if args.target == "equipped":
        for i, it in enumerate(rank_equipped(state, totals), 1):
            print(f"{i}위 [{it['slot']}] {it['gem']}: {it['guild']:.2f}%/길드, {it['world']:.2f}%/월드")
    else:
        for i, it in enumerate(rank_inventory(state, totals), 1):
            print(f"{i}위 {it['gem']}: {it['guild']:.2f}%/길드, {it['world']:.2f}%/월드")


def cmd_add(args, state):
    state["inventory"].append(args.gem)
    save_state(state)

    totals = total_from_state(state)
    guild, world = gem_eff(args.gem, totals)
    inv_ranked = rank_inventory(state, totals)
    pos = next(i for i, it in enumerate(inv_ranked, 1) if it["gem"] == args.gem and it["guild"] == guild)

    print(f"{args.gem} 인벤토리에 추가 완료.")
    print(f"효율: {guild:.2f}%/길드, {world:.2f}%/월드")
    print(f"인벤토리 내 순위: {pos}위 / 전체 {len(inv_ranked)}개")

    base = baseline_gem(rank_equipped(state, totals))
    print(f"기준보석 {base['gem']}({base['guild']:.2f}%)보다 효율이 {_cmp_word(guild, base['guild'])}.")


def cmd_reroll_avg(args, state):
    totals = total_from_state(state)
    combo_types = combo_to_types(args.combo)
    if not combo_types:
        raise SystemExit("조합을 인식할 수 없습니다. 예: 공속속")

    avg, mx = reroll_combo_stats(combo_types, totals, state["option_ranges"])
    print(f"[{args.combo}] 조합 - 평균 효율: {avg:.2f}%/길드, 최댓값: {mx:.2f}%/길드")

    sorted_combo = sorted(combo_types)
    found = False
    for slot, gems in state["equipped"].items():
        for idx, g in enumerate(gems, start=1):
            if sorted(parse_types(g)) == sorted_combo:
                found = True
                guild, _world = gem_eff(g, totals)
                verdict, prem = reroll_verdict(guild, avg, state)
                suffix = f" (프리미엄 {prem:.2f}배)" if prem is not None else ""
                print(f"- [장착:{slot} {idx}번] {g} (현재 {guild:.2f}%/길드): {verdict}{suffix}")
    for g in state["inventory"]:
        if sorted(parse_types(g)) == sorted_combo:
            found = True
            guild, _world = gem_eff(g, totals)
            verdict, prem = reroll_verdict(guild, avg, state)
            suffix = f" (프리미엄 {prem:.2f}배)" if prem is not None else ""
            print(f"- [인벤토리] {g} (현재 {guild:.2f}%/길드): {verdict}{suffix}")

    if not found:
        print("현재 보유 중인 동일 조합 보석이 없습니다.")


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(description="마지막이야기 보석 계산기")
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("eval", help="보석 효율/리롤 가치 평가")
    p_eval.add_argument("gem")
    p_eval.add_argument("price", type=float, nargs="?", help="구매 가격(억)")
    p_eval.set_defaults(func=cmd_eval)

    p_equip = sub.add_parser("equip", help="보석 장착 교체")
    p_equip.add_argument("slot")
    p_equip.add_argument("index", type=int, help="1부터 시작하는 슬롯 번호")
    p_equip.add_argument("new_gem")
    p_equip.set_defaults(func=cmd_equip)

    p_rank = sub.add_parser("rank", help="순위 출력")
    p_rank.add_argument("target", choices=["equipped", "inventory"])
    p_rank.set_defaults(func=cmd_rank)

    p_add = sub.add_parser("add", help="인벤토리에 보석 추가")
    p_add.add_argument("gem")
    p_add.set_defaults(func=cmd_add)

    p_reroll = sub.add_parser("reroll_avg", help="옵션 조합 리롤 평균효율 계산")
    p_reroll.add_argument("combo", help="예: 공속속")
    p_reroll.set_defaults(func=cmd_reroll_avg)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    state = load_state()
    args.func(args, state)


if __name__ == "__main__":
    main()
