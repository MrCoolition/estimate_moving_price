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
   - **Start Command:** `uvicorn main:api --host 0.0.0.0 --port $PORT`
     - Do **not** replace `$PORT` with a fixed value. Render sets this
       environment variable automatically. If the service doesn't listen
       on that port, Render's health check will kill the process.
4. **Select Plan and Deploy**
   - Choose a free or paid plan based on traffic needs.
   - Click **Create Web Service**. Render will build and start the service.
5. **Test the Endpoint**
   - Once deployed, visit `https://estimate-moving-price.onrender.com/docs` to view the OpenAPI docs and verify `/estimate` works.
6. **Use with ElevenLabs AI Agent**
   - Register a new tool in the ElevenLabs interface using the base URL of your Render service and the endpoint `POST /estimate`.
   - The complete webhook configuration is provided in `elevenlabs_tool.json`. Import this file directly or copy its contents when creating the tool.

## ElevenLabs Tool Form Example

When creating the webhook tool in the ElevenLabs UI, fill out the form using these values:

- **Name**: `Estimate_Move_Price`
- **Description**: Determine the estimated cost of the move based on the caller's inventory, distance and move date.
- **Method**: `POST`
- **URL**: `https://estimate-moving-price.onrender.com/estimate`
- **Response timeout**: `10`
- **Authentication**: none
- **Headers**: leave blank
- **Path parameters**: none
- **Query parameters**: none
- **Request body**: JSON object containing `items`, `distance_miles`, and `move_date`.

This mirrors the contents of `elevenlabs_tool.json` and allows the agent to call the service with dynamic values.

Your FastAPI service is now ready to be called by the agent.

## Data Files

This repo now includes sample data used for estimating move costs.
- `data/estimation_weights_volumes_categories.json` contains item weights and volumes.
- `data/moving_rules.json` describes pricing and operational rules.
- `data/box_carton_information.txt` and `data/packing_weight_volume_pricing.tsv` provide box and packing details.
- `data/follow_up_questions.txt` lists questions the agent can use when gathering details.


## API Usage

Send a `POST` request to `/estimate` with a JSON body specifying the items to move, distance in miles, and the date of the move. Item names are matched against `data/estimation_weights_volumes_categories.json`; close matches are accepted so minor variations (e.g., "grand piano" vs "piano - grand") will still resolve. Quantities must be positive integers or the API will return `400 Quantity must be positive`.

Example request:

```json
{
  "items": {"bed_king_mattress": 1, "bar_stool": 4},
  "distance_miles": 15,
  "move_date": "2025-07-08"
}
```

The body should be a JSON **object** with these fields. If the request arrives
as an array containing a single object (as some tooling formats requests), the
service will unwrap it automatically. Any other structure will be rejected with
a `400` error.

The service also accepts a variant where `distance_miles` and `move_date`
appear inside the `items` object. These fields will be extracted automatically
for compatibility with agent tools that send all values together.

The response includes the total cost and a breakdown of labor hours, protective materials charges, number of movers and trucks, and the calculated weight and volume.

## Run Locally

1. `cd` into the repository folder.
2. `pip install -r requirements.txt`
3. `uvicorn main:api --host 0.0.0.0 --port 8000`

Once running, open [http://localhost:8000/docs](http://localhost:8000/docs) to test the API.
