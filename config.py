"""Settings for the BLR flight and weather streaming project.

Every value can be overridden with an environment variable of the same name.
"""
import os

# ---------------------------------------------------------------------------
# Airport
# ---------------------------------------------------------------------------
AIRPORT = {
    "iata": "BLR",
    "icao": "VOBL",
    "name": "Kempegowda International Airport, Bengaluru",
    "lat": 13.1986,
    "lon": 77.7066,
}

# Two parallel runways. Heading is the "09" end in degrees; the "27" end is +180.
# Assumption used by this project: arrivals on the north runway, departures on the
# south runway (segregated mode). Change "use" here if the airport changes its mode.
# Threshold positions (lat, lon) of each end, from the published aerodrome data; used by the map.
RUNWAYS = [
    {"name": "09L/27R", "heading": 90, "use": "ARR",
     "ends": {"09L": (13.207164, 77.686073), "27R": (13.206847, 77.722969)}},
    {"name": "09R/27L", "heading": 90, "use": "DEP",
     "ends": {"09R": (13.189734, 77.689980), "27L": (13.189414, 77.726875)}},
]
PREFERRED_END = "09"          # end used when the wind is calm or straight across

# Capacity model (movements per runway per hour). These are planning assumptions
# for the dashboard, not the airport's declared figures.
RUNWAY_CAPACITY_PER_HOUR = int(os.getenv("RUNWAY_CAPACITY_PER_HOUR", "30"))
CAPACITY_FACTOR = {"VFR": 1.0, "MVFR": 0.9, "IFR": 0.75, "LIFR": 0.6}
THUNDERSTORM_FACTOR = 0.7     # applied on top when TS is reported

# ---------------------------------------------------------------------------
# Metric thresholds
# ---------------------------------------------------------------------------
ON_TIME_MINUTES = 15          # a flight is on time if it is less than 15 min late
CROSSWIND_LIMIT_KT = 20       # ops alert above this crosswind component
BUSY_LEVELS = [(90, "Saturated"), (75, "High"), (50, "Moderate"), (0, "Low")]  # % of capacity

# ---------------------------------------------------------------------------
# Kafka
# ---------------------------------------------------------------------------
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9094")
TOPIC_FLIGHTS = os.getenv("TOPIC_FLIGHTS", "flights.raw")   # flight status events
TOPIC_WEATHER = os.getenv("TOPIC_WEATHER", "weather.raw")   # METAR observations
TOPIC_POSITIONS = os.getenv("TOPIC_POSITIONS", "positions.raw")  # live aircraft positions near BLR
TOPIC_DLQ = os.getenv("TOPIC_DLQ", "airport.dlq")           # messages that failed validation

# Two consumer groups, so each consumer gets its own copy of every message.
GROUP_PUBLIC = os.getenv("GROUP_PUBLIC", "public_board_group")
GROUP_OPS = os.getenv("GROUP_OPS", "ops_control_group")

# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
PG_DSN = os.getenv("PG_DSN", "postgresql://flight:flight@localhost:5432/flight_db")

# ---------------------------------------------------------------------------
# Apache Flink (flink_runway_job.py) - a third consumer group on flights.raw
# ---------------------------------------------------------------------------
GROUP_FLINK = os.getenv("GROUP_FLINK", "flink_runway_group")
# Inside docker compose Flink reaches Kafka as kafka:9092 and PostgreSQL as postgres:5432.
FLINK_KAFKA_BROKER = os.getenv("FLINK_KAFKA_BROKER", KAFKA_BROKER)
FLINK_JDBC_URL = os.getenv("FLINK_JDBC_URL", "jdbc:postgresql://localhost:5432/flight_db")
FLINK_JDBC_USER = os.getenv("FLINK_JDBC_USER", "flight")
FLINK_JDBC_PASSWORD = os.getenv("FLINK_JDBC_PASSWORD", "flight")

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
# Flights: AviationStack (free key from aviationstack.com). Without a key the
# producer uses the offline schedule simulator.
AVIATIONSTACK_KEY = os.getenv("AVIATIONSTACK_KEY", "")
AVIATIONSTACK_URL = "http://api.aviationstack.com/v1/flights"

# Weather: METAR from the Aviation Weather Center (no key needed).
METAR_URL = "https://aviationweather.gov/api/data/metar"

# Live aircraft positions: adsb.lol (ADS-B, no key needed). Radius in nautical miles.
ADSB_URL = "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}"
POSITIONS_RADIUS_NM = int(os.getenv("POSITIONS_RADIUS_NM", "32"))      # about 60 km
POSITIONS_POLL_SECONDS = int(os.getenv("POSITIONS_POLL_SECONDS", "10"))
MAP_RADIUS_KM = 60

API_TIMEOUT = float(os.getenv("API_TIMEOUT", "6"))
FLIGHT_POLL_SECONDS = int(os.getenv("FLIGHT_POLL_SECONDS", "60"))
WEATHER_POLL_SECONDS = int(os.getenv("WEATHER_POLL_SECONDS", "600"))

# Flights published by the producer: from 24 h ago to 12 h ahead.
WINDOW_PAST_HOURS = 24
WINDOW_AHEAD_HOURS = 12

# ---------------------------------------------------------------------------
# Web dashboard (web_dashboard.py) - reads the views as a read-only user
# ---------------------------------------------------------------------------
DASHBOARD_DSN = os.getenv("DASHBOARD_DSN",
                          "postgresql://dashboard_ro:dashboard_ro@localhost:5432/flight_db")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5000"))
