# Niche Intelligence — public deploy (Python 3.12)
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached unless requirements change).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

# The host sets $PORT (Render/Railway/Fly do this automatically).
# The app refuses to start in public mode without NI_ADMIN_PASSWORD.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
