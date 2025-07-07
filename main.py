from fastapi import FastAPI
from pydantic import BaseModel

api = FastAPI()

class EstimateRequest(BaseModel):
    volume: float  # cubic feet
    distance: float  # miles

class EstimateResponse(BaseModel):
    cost: float

@api.post('/estimate', response_model=EstimateResponse)
def estimate(request: EstimateRequest):
    # simple cost calculation
    base_rate = 100
    volume_rate = 1.5
    distance_rate = 0.5
    cost = base_rate + request.volume * volume_rate + request.distance * distance_rate
    return {'cost': round(cost, 2)}
