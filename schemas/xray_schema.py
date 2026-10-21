from pydantic import BaseModel

class PostXraySchema(BaseModel):
    user_id: str
    foto_base64: str