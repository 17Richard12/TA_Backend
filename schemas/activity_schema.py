from pydantic import BaseModel
from typing import List, Optional

class GenerateActivitySchema(BaseModel):
    user_uid: str
    timestamp: str

class ActivityItem(BaseModel):
    id: str
    activity: str
    done: bool

class DailyActivityResponse(BaseModel):
    id: str
    user_uid: str
    timestamp: str
    activities: List[ActivityItem]