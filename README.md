# Render Service Setup for Moving Estimate Tool

This repository holds the FastAPI code used to estimate moving costs. Below are quick setup instructions for deploying the API as a web service on [Render](https://render.com) so it can be used as a custom tool in the ElevenLabs AI Agent system.

## Prerequisites

- A Render account
- A Git repository containing your FastAPI code (this repository)
- Optional: `requirements.txt` listing `fastapi`, `uvicorn`, and any other dependencies

## Steps

1. **Log in to Render**
   - Go to <https://dashboard.render.com>, sign up or sign in.
2. **Create a New Web Service**
   - Click **New** > **Web Service**.
   - Connect your GitHub or GitLab account and pick this repository.
3. **Configure Build and Start Commands**
   - **Environment**: Python 3.11 or your preferred version.
   - **Build Command:** `pip install -r requirements.txt` (make sure `requirements.txt` exists and lists `fastapi` and `uvicorn`).
   - **Start Command:** `uvicorn main:api --host 0.0.0.0 --port 8000` (adjust `main` if your app is in a different module).
4. **Select Plan and Deploy**
   - Choose a free or paid plan based on traffic needs.
   - Click **Create Web Service**. Render will build and start the service.
5. **Test the Endpoint**
   - Once deployed, visit `https://<your-service>.onrender.com/docs` to view the OpenAPI docs and verify `/estimate` works.
6. **Use with ElevenLabs AI Agent**
   - In the ElevenLabs interface, register a new tool with the base URL of your Render service. Set the endpoint to `POST /estimate` and describe the expected payload.

   Example webhook tool configuration:

   ```json
   {
     "name": "Estimate_Move_Price",
     "description": "Determine the estimated cost of the move based on the caller's inventory, distance and move date.",
     "type": "webhook",
     "api_schema": {
      "url": "https://estimate-moving-price.onrender.com/estimate",
       "method": "POST",
       "path_params_schema": [],
       "query_params_schema": [],
       "request_body_schema": {
         "id": "estimateRequest",
         "description": "Data required to generate a moving estimate",
         "type": "object",
         "required": true,
         "properties": [
           {
             "id": "items",
             "type": "object",
             "description": "Mapping of items to move with their quantities",
             "properties": [],
             "dynamic_variable": "items",
             "required": true
           },
           {
             "id": "distance_miles",
             "type": "number",
             "description": "Distance of the move in miles",
             "dynamic_variable": "distance_miles",
             "required": true
           },
           {
             "id": "move_date",
             "type": "string",
             "format": "date",
             "description": "Date of the move (YYYY-MM-DD)",
             "dynamic_variable": "move_date",
             "required": true
           }
         ]
       },
       "request_headers": [],
       "auth_connection": null
     },
     "response_timeout_secs": 10,
     "dynamic_variables": {
      "dynamic_variable_placeholders": {
        "base_url": "https://estimate-moving-price.onrender.com"
      }
     }
   }
   ```

Your FastAPI service is now ready to be called by the agent.

## Data Files

This repo now includes sample data used for estimating move costs.
- `data/estimation_weights_volumes_categories.json` contains item weights and volumes.
- `data/moving_rules.json` describes pricing and operational rules.
- `data/box_carton_information.txt` and `data/packing_weight_volume_pricing.tsv` provide box and packing details.
- `data/follow_up_questions.txt` lists questions the agent can use when gathering details.


## API Usage

Send a `POST` request to `/estimate` with a JSON body specifying the items to move, distance in miles, and the date of the move. Item names must match entries in `data/estimation_weights_volumes_categories.json` (aliases are accepted).

Example request:

```json
{
  "items": {"bed_king_mattress": 1, "bar_stool": 4},
  "distance_miles": 15,
  "move_date": "2025-07-08"
}
```

The response includes the total cost and a breakdown of labor hours, protective materials charges, number of movers and trucks, and the calculated weight and volume.
