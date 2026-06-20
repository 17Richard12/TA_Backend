from pydantic import BaseModel, Field

class SmokeCountSchema(BaseModel):
    user_id: str = Field(..., alias="userid")
    timestamp: str  # Format: dd/mm/yyyy
    total: int      # Jumlah rokok yang dikirim dari frontend

    class Config:
        populate_by_name = True