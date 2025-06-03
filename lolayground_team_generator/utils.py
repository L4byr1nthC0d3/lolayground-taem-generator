# utils.py
import random
from typing import List, Dict, Tuple
from sqlalchemy.orm import Session
from models import Player, Position, Tier, Match, TeamAssignment
from itertools import combinations, permutations

# 고정된 라인 순서
POSITIONS_ORDER = [Position.TOP, Position.JUNGLE, Position.MID, Position.ADC, Position.SUPPORT]


def calculate_tier_score(tier: str, division: int, lp: int = 0) -> float:
    base_scores = {
        "IRON": 0, "BRONZE": 400, "SILVER": 800, "GOLD": 1200,
        "PLATINUM": 1600, "EMERALD": 2000, "DIAMOND": 2400,
        "MASTER": 2800, "GRANDMASTER": 2900, "CHALLENGER": 3000
    }
    if tier in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
        return base_scores[tier] + division
    division_value = (4 - division) * 100
    lp_value = lp  # tier_score 계산 시 LP는 그대로 더하거나, 비중을 줄일 수 있음 (여기서는 그대로 사용)
    # lp_value = lp / 100.0 # 0-1 사이 값으로 사용 시
    return base_scores[tier] + division_value + lp_value


def get_player_position_fit_score(player: Player, target_position: Position) -> int:
    """플레이어가 특정 라인에 배정될 때의 선호도 점수 계산"""
    score = 0
    if player.position == target_position:
        score = 100
    elif player.sub_position and player.sub_position == target_position:
        score = 70
    elif player.position == Position.ALL:
        score = 40  # 주 포지션이 ALL이면 어떤 라인이든 기본 적합도
    elif player.sub_position and player.sub_position == Position.ALL:
        score = 20
    return score


def find_best_lineup(team_players: List[Player]) -> Tuple[List[Player], int, float]:
    """
    5명의 플레이어 그룹에 대해, 고정된 5개 라인에 배정했을 때
    최대의 포지션 적합도(선호도) 합계를 가지는 라인업과 그 점수, 팀 평균 티어 점수를 반환.
    반환되는 플레이어 리스트는 POSITIONS_ORDER 순서대로 배정된 것임.
    """
    best_lineup_players_in_order = []
    max_total_fit_score = -1  # 선호도 점수 합계

    if not team_players or len(team_players) != 5:
        return [], 0, 0

    # 팀의 평균 '티어 점수' 계산 (밸런싱의 기본 지표)
    team_avg_tier_score = sum(p.tier_score for p in team_players) / len(team_players)

    for p_permutation in permutations(team_players, len(POSITIONS_ORDER)):
        current_total_fit_score = 0
        for i, player_in_pos in enumerate(p_permutation):
            target_pos = POSITIONS_ORDER[i]
            current_total_fit_score += get_player_position_fit_score(player_in_pos, target_pos)

        if current_total_fit_score > max_total_fit_score:
            max_total_fit_score = current_total_fit_score
            best_lineup_players_in_order = list(p_permutation)

    return best_lineup_players_in_order, max_total_fit_score, team_avg_tier_score


def balance_teams(players: List[Player]) -> Tuple[List[Player], List[Player]]:
    if len(players) != 10:
        raise ValueError("정확히 10명의 플레이어가 필요합니다")

    best_overall_blue_team_ordered = []
    best_overall_red_team_ordered = []
    # 최종 밸런스 점수: |블루팀 평균 티어점수 - 레드팀 평균 티어점수| / (1 + 블루팀 포지션 적합도 총점 + 레드팀 포지션 적합도 총점)
    # 이 값이 낮을수록 좋음.
    min_final_balance_metric = float('inf')

    all_team_combinations = list(combinations(players, 5))

    for i in range(len(all_team_combinations) // 2):
        blue_candidate_players = list(all_team_combinations[i])
        red_candidate_players = [p for p in players if p not in blue_candidate_players]

        blue_lineup_ordered, blue_total_fit, blue_avg_tier = find_best_lineup(blue_candidate_players)
        red_lineup_ordered, red_total_fit, red_avg_tier = find_best_lineup(red_candidate_players)

        if not blue_lineup_ordered or not red_lineup_ordered:
            continue

        tier_score_difference = abs(blue_avg_tier - red_avg_tier)

        # 포지션 적합도가 높을수록, 티어 점수 차이가 작을수록 좋은 밸런스
        current_balance_metric = tier_score_difference / (1 + blue_total_fit + red_total_fit)

        if current_balance_metric < min_final_balance_metric:
            min_final_balance_metric = current_balance_metric
            best_overall_blue_team_ordered = blue_lineup_ordered
            best_overall_red_team_ordered = red_lineup_ordered

    if not best_overall_blue_team_ordered or not best_overall_red_team_ordered:
        # 만약 위 로직으로 팀을 찾지 못했다면 (매우 드문 경우)
        players_sorted_by_tier = sorted(players, key=lambda p: p.tier_score, reverse=True)
        blue_fallback = [players_sorted_by_tier[i] for i in [0, 2, 4, 6, 8]]
        red_fallback = [players_sorted_by_tier[i] for i in [1, 3, 5, 7, 9]]
        # fallback 팀은 라인업 순서가 보장되지 않으므로, 실제로는 find_best_lineup을 한번 더 호출해야함
        # 여기서는 단순화하여 그대로 반환 (실제로는 POSITIONS_ORDER에 맞춰 재배열 필요)
        # 하지만 10명이면 위 로직에서 거의 항상 팀이 찾아짐.
        return blue_fallback, red_fallback

    return best_overall_blue_team_ordered, best_overall_red_team_ordered


def update_team_match_scores(winning_team_side: str, match_id: int, db: Session):
    """
    팀 단위 승/패 처리 및 매치 점수 업데이트.
    플레이어가 배정된 라인과 주/부 포지션 일치 여부에 따라 점수 변동폭 차등.
    """
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or match.is_completed:
        return False  # 이미 처리되었거나 매치가 없음

    # 각 팀의 TeamAssignment 목록 (플레이어 ID와 배정된 라인 포함)
    # TeamAssignment 생성 시 라인 순서대로 id가 매겨졌다고 가정하지 않고,
    # assigned_position 필드를 직접 사용.
    winning_team_assignments = db.query(TeamAssignment).filter_by(match_id=match_id, team=winning_team_side).all()
    losing_team_side = "RED" if winning_team_side == "BLUE" else "BLUE"
    losing_team_assignments = db.query(TeamAssignment).filter_by(match_id=match_id, team=losing_team_side).all()

    # 점수 변동폭 설정
    score_changes = {
        "main_win": 35, "main_lose": -25,
        "sub_win": 30, "sub_lose": -30,
        "other_win": 25, "other_lose": -20,
        "all_pos_win": 30, "all_pos_lose": -30,  # 주 포지션이 ALL인 경우
    }

    for assignment in winning_team_assignments:
        player = db.query(Player).get(assignment.player_id)
        if not player: continue

        assigned_pos = assignment.assigned_position  # TeamAssignment에 저장된 실제 배정 라인
        change_key_suffix = "_win"

        score_change = 0
        if player.position == Position.ALL:  # 주 포지션이 ALL인 경우
            score_change = score_changes["all_pos_win"]
        elif player.position == assigned_pos:  # 주 포지션 일치
            score_change = score_changes["main_win"]
        elif player.sub_position and player.sub_position == assigned_pos:  # 부 포지션 일치
            score_change = score_changes["sub_win"]
        else:  # 기타 포지션
            score_change = score_changes["other_win"]

        player.match_score += score_change
        player.win_count += 1

    for assignment in losing_team_assignments:
        player = db.query(Player).get(assignment.player_id)
        if not player: continue

        assigned_pos = assignment.assigned_position
        change_key_suffix = "_lose"

        score_change = 0
        if player.position == Position.ALL:
            score_change = score_changes["all_pos_lose"]
        elif player.position == assigned_pos:
            score_change = score_changes["main_lose"]
        elif player.sub_position and player.sub_position == assigned_pos:
            score_change = score_changes["sub_lose"]
        else:
            score_change = score_changes["other_lose"]

        player.match_score += score_change
        player.lose_count += 1

    match.winner = winning_team_side
    match.is_completed = True
    db.commit()
    return True

# update_match_score 함수는 이제 update_team_match_scores로 대체되므로 제거하거나 주석처리 가능
# def update_match_score(winner_id: int, loser_id: int, db: Session): ...