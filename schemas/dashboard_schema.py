from pydantic import BaseModel
from typing import List

class HourlyLog(BaseModel):
    hour: str
    count: int

class DashboardResponse(BaseModel):
    smoke_count_total: int
    smoke_limit_low: int = 5
    smoke_limit_high: int = 10
    daily_activities_done_total: int
    chat_total: int
    smoke_hourly_graph: List[HourlyLog]
    activities_done_list: List[str]
