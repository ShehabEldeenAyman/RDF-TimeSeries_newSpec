from fastapi import FastAPI, APIRouter

app = FastAPI()

# Every route made with "api" gets /api in front of its address
api = APIRouter(prefix="/api")

@api.get("/endpoints")
def list_endpoints():
    return [{"id": "waterinfo_289423042_tss2", "label": "Waterinfo EC20, Grootdok"}]


@api.get("/endpoints/{endpoint_id}")
def get_endpoint(endpoint_id):
    return {"id": endpoint_id}

# Only now, after all routes are made: add them to the app
app.include_router(api)