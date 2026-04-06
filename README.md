# CALAMITY-MONITOR

Calamity Monitor is a real-time disaster intelligence dashboard with a FastAPI backend and a React-based frontend.

## What this project does

- Tracks major global calamity events from trusted public sources.
- Visualizes events on an interactive 3D globe.
- Streams high-severity alerts in near real time.
- Shows event intelligence like severity, type, magnitude, impact, and geospatial context.
- Provides live feed cards with source links, media links, and event filtering.

## Core functions

### 1. Real-time event monitoring

- Polls external feeds at regular intervals.
- Normalizes events into a common structure.
- Keeps a recent in-memory event cache for fast responses.

### 2. Flash alerts via WebSocket

- Detects newly arrived high-severity events.
- Broadcasts flash alerts instantly to connected clients.
- Keeps the UI synced with new critical incidents.

### 3. Interactive globe intelligence view

- Plots events using severity-based color coding.
- Shows ripple effects for urgent incidents.
- Supports event selection, auto-focus, and contextual marker labels.

### 4. Location and geospatial enrichment

- Reverse geocodes selected event coordinates into place details.
- Supports location-based event search with radius filtering.
- Calculates distance from searched place to nearby events.

### 5. Live feed and filtering

- Displays event-linked news cards.
- Filters feed by disaster category.
- Supports pagination for readable browsing on both desktop and mobile.

### 6. Event severity model

- Assigns severity tiers using event attributes (for example magnitude/alert level).
- Uses consistent levels: red, orange, yellow, green.

### 7. Resilient data handling

- Handles partial source failures gracefully.
- Continues serving cached results when sources are unavailable.
- Provides health and status visibility through API endpoints.

## Backend API capabilities

- Health status reporting.
- Event retrieval with optional severity and type filters.
- News retrieval from cached aggregated results.
- Geospatial search around user-provided locations.
- Real-time event streaming through a WebSocket endpoint.

## Frontend capabilities

- Responsive dashboard optimized for desktop and mobile.
- Search suggestions and recent location history.
- Real-time status indicators for data stream and clock.
- Side-panel intelligence cards for selected incidents.

## Tech stack

- Frontend: React (UMD + Babel), Globe.gl, plain CSS.
- Backend: FastAPI, asyncio, httpx, xmltodict, geopy.
- Data flow: polling + in-memory cache + WebSocket push.
