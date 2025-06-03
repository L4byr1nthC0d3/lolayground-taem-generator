# schemas.py
from pydantic import BaseModel, validator, ConfigDict
from typing import List, Optional
from datetime import datetime
from enum import Enum


class TokenData(BaseModel):
    user_id: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str


class UserBase(BaseModel):
    user_id: str


class User(UserBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class PositionEnum(str, Enum):
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MID = "MID"
    ADC = "ADC"
    SUPPORT = "SUPPORT"
    ALL = "ALL"


class TierEnum(str, Enum):
    IRON = "IRON"
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"
    EMERALD = "EMERALD"
    DIAMOND = "DIAMOND"
    MASTER = "MASTER"
    GRANDMASTER = "GRANDMASTER"
    CHALLENGER = "CHALLENGER"


class PlayerBase(BaseModel):
    nickname: str
    tier: TierEnum
    division: int
    position: PositionEnum  # 주 포지션
    sub_position: Optional[PositionEnum] = None  # 부 포지션
    lp: Optional[int] = 0

    @validator('division', pre=True, always=True)
    def validate_division_by_tier(cls, v, values):
        tier_value = values.get('tier')
        if tier_value:
            if tier_value in ['MASTER', 'GRANDMASTER', 'CHALLENGER']:
                if v < 0:  # LP는 0 이상
                    raise ValueError('LP(점수)는 0 이상이어야 합니다')
            elif v not in [1, 2, 3, 4]:
                raise ValueError('디비전은 1, 2, 3, 4 중 하나여야 합니다')
        return v

    @validator('sub_position', pre=True, always=True)
    def validate_sub_position_logic(cls, v, values):
        main_position = values.get('position')

        if main_position:
            # 주 포지션이 ALL이 아닌데 부 포지션이 ALL인 경우
            if main_position != PositionEnum.ALL and v == PositionEnum.ALL:
                raise ValueError("주 포지션이 특정 라인이면 부 포지션은 'ALL'일 수 없습니다.")

            # 주 포지션과 부 포지션이 동일한 경우 (단, 둘 다 ALL인 경우는 제외)
            if v and main_position == v and main_position != PositionEnum.ALL:
                raise ValueError('주 포지션과 부 포지션은 같을 수 없습니다 (ALL 제외).')

            # 주 포지션이 ALL이면 부 포지션은 없어야 함 (UI에서 제어하므로, API 레벨에서는 선택적)
            # if main_position == PositionEnum.ALL and v is not None:
            #     # UI에서 주 포지션이 ALL이면 부 포지션을 '' (None)으로 보내므로 이 검사는 통과됨
            #     # 만약 값을 보낸다면 여기서 에러 발생
            #     raise ValueError("주 포지션이 'ALL'이면 부 포지션을 선택할 수 없습니다.")
        return v


class PlayerCreate(PlayerBase):
    pass


class Player(PlayerBase):
    id: int
    tier_score: float
    match_score: float
    win_count: int
    lose_count: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TeamAssignmentBase(BaseModel):
    team: str
    player_id: int


class TeamCreate(TeamAssignmentBase):
    pass


class TeamAssignment(TeamAssignmentBase):
    id: int
    match_id: int
    model_config = ConfigDict(from_attributes=True)


class MatchBase(BaseModel):
    pass


class MatchCreate(MatchBase):
    player_ids: List[int]


class Match(MatchBase):
    id: int
    match_date: datetime
    blue_team_avg_score: float
    red_team_avg_score: float
    blue_team_match_score: float
    red_team_match_score: float
    balance_score: float
    winner: Optional[str] = None
    is_completed: bool = False
    model_config = ConfigDict(from_attributes=True)


class MatchWithTeams(Match):
    blue_team: List[Player]
    red_team: List[Player]
    model_config = ConfigDict(from_attributes=True)


class MatchResult(BaseModel):
    match_id: int
    winner: str