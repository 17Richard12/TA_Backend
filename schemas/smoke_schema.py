from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_smoke_date_str(date_str: str) -> str:
    """
    Validate and normalize a date string to YYYY-MM-DD format.
    Accepts YYYY-MM-DD or DD/MM/YYYY.
    """
    if not isinstance(date_str, str) or not date_str.strip():
        raise ValueError("Format tanggal tidak valid.")
    cleaned = date_str.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError("Format tanggal harus YYYY-MM-DD atau DD/MM/YYYY.")


class SmokeCountSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(..., alias="userid", min_length=1)
    timestamp: str  # Format: YYYY-MM-DD or DD/MM/YYYY
    total: int = Field(..., ge=0, le=200)  # Jumlah rokok (0 - 200, tidak boleh negatif)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        return normalize_smoke_date_str(v)
