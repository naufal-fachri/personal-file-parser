# 📂 File Parser API

A document parsing and extraction service built with FastAPI. Handles document ingestion, text extraction, and vector storage using MinIO (object storage) and Qdrant (vector database). Redis is shared with the OCR Service — no separate setup needed.

---

## 🏗️ Architecture

```
                        ┌──────────────────────────┐
                        │      File Parser API      │
                        │        (FastAPI)           │
                        └────────────┬─────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    ▼                                 ▼
      ┌───────────────────────┐         ┌────────────────────────────┐
      │         MinIO         │         │          Qdrant            │
      │    (Object Storage)   │         │     (Vector Database)      │
      │   :9000 / :9001       │         │  Self-hosted or Cloud      │
      └───────────────────────┘         └────────────────────────────┘
              │                                      │
        Store raw files                      Store embeddings
        & documents                          for semantic search


                        ┌──────────────────────────┐
                        │          Redis           │
                        │   (Shared with OCR Svc)  │
                        │         :6380            │
                        └──────────────────────────┘
```

The API receives documents, extracts text via the `services/extract.py` pipeline, stores raw files in **MinIO**, and indexes embeddings into **Qdrant** for semantic search. **Redis** is shared with the OCR Service — refer to the OCR Service README for its setup. Google Gemini is used for AI-powered text processing.

---

## 🚀 Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.10+ with `uv`

---

## 🔧 Required Infrastructure Setup

Before running the File Parser API, ensure **MinIO** and **Qdrant** are running.

> 📌 **Redis** is already set up as part of the OCR Service — no need to run it again. Just make sure the OCR Service Redis instance is up and point this service to it via `.env`.

---

### 1. MinIO (Object Storage)

MinIO is an S3-compatible object storage used to store raw documents.

Choose one of the two setups below depending on whether you're using TLS.

---

#### Option A — Without TLS (HTTP, simpler setup)

```bash
docker run -d --name minio \
  -p 9000:9000 \
  -p 9001:9001 \
  -e MINIO_ROOT_USER=your_username \
  -e MINIO_ROOT_PASSWORD=your_password \
  -v ${PWD}/minio_data:/data \
  quay.io/minio/minio server /data --console-address ":9001"
```

If you're **not using a certificate**, you need to make two changes:

**1. In your application code** — comment out the `http_client` and set `secure=False` when initializing the MinIO client:

```python
# With TLS (default):
# client = Minio(
#     endpoint=settings.MINIO_ENDPOINT,
#     access_key=settings.MINIO_ACCESS_KEY,
#     secret_key=settings.MINIO_SECRET_KEY,
#     http_client=urllib3.PoolManager(
#         cert_reqs="CERT_REQUIRED",
#         ca_certs=settings.CA_CERTS_PATH,
#     ),
#     secure=True,
# )

# Without TLS — use this instead:
client = Minio(
    endpoint=settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    secure=False,
)
```

**2. In `docker-compose.yaml`** — remove the certificate volume mount since no cert is needed:

```yaml
# With TLS (default):
services:
  file-parser:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: personal-file-parser
    ports:
      - "8002:8000"
    env_file:
      - .env
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ${HOME}/minio/certs/public.crt:/etc/minio/certs/public.crt:ro  # ← remove this line
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

# Without TLS — remove the volumes block entirely:
services:
  file-parser:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: personal-file-parser
    ports:
      - "8002:8000"
    env_file:
      - .env
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

---

#### Option B — With TLS (HTTPS, recommended for production)

MinIO automatically enables TLS when it detects `public.crt` and `private.key` in the `--certs-dir` path inside the container. The recommended cert directory to mount is `/etc/minio/certs`.

| File | Description |
|------|-------------|
| `public.crt` | TLS certificate (or full chain) |
| `private.key` | Private key matching the certificate |

**Step 1 — Generate self-signed certs (for dev/testing) using `certgen`:**

```bash
# Install certgen
curl -L https://github.com/minio/certgen/releases/latest/download/certgen-linux-amd64 -o certgen
chmod +x certgen
sudo mv certgen /usr/local/bin/

# Generate certs — include all hostnames/IPs clients will use to connect
mkdir -p $HOME/minio/certs
cd $HOME/minio/certs
certgen -host "127.0.0.1,localhost"
```

This creates `public.crt` and `private.key` in `$HOME/minio/certs/`.

For production, replace this step with certificates from your organization's CA or a trusted third-party CA.

**Step 2 — Run MinIO with certs mounted:**

```bash
docker run -dt --name minio \
  -p 9000:9000 \
  -p 9001:9001 \
  -e MINIO_ROOT_USER=your_username \
  -e MINIO_ROOT_PASSWORD=your_password \
  -v $HOME/minio/data:/mnt/data \
  -v $HOME/minio/certs:/etc/minio/certs \
  quay.io/minio/minio server /mnt/data \
  --certs-dir /etc/minio/certs \
  --console-address ":9001"
```

MinIO detects `public.crt` and `private.key` in `--certs-dir` and automatically switches to HTTPS.

**Step 3 — (Optional) Trust additional CAs:**

If MinIO needs to connect to other services using self-signed certs (e.g. MinIO KMS), place those CA certs in a `CAs/` subfolder:

```bash
mkdir -p $HOME/minio/certs/CAs
cp /path/to/ca-certificate.crt $HOME/minio/certs/CAs/
```

The `CAs/` directory is picked up automatically when the parent `certs/` directory is mounted.

**Step 4 — Connect using HTTPS:**

Once TLS is enabled, access the Console at `https://localhost:9001`. For self-signed certs, your browser will show a security warning — you can accept it or add the cert to your system's trusted certificates.

The `docker-compose.yaml` already mounts `$HOME/minio/certs/public.crt` into the API container at `/etc/minio/certs/public.crt:ro` so the app can verify the MinIO TLS certificate — no changes needed there.

> 📖 Full TLS setup guide: https://docs.min.io/enterprise/aistor-object-store/installation/container/network-encryption/

---

| Port | Description |
|------|-------------|
| `9000` | S3 API endpoint (used by the application) |
| `9001` | Web Console UI — `http://localhost:9001` |

> 📌 After starting MinIO, log in to the console at `http://localhost:9001` and create the bucket(s) your application expects.
>
> 📖 For full documentation, visit: https://docs.min.io/enterprise/aistor-object-store/

---

### 2. Qdrant (Vector Database)

Qdrant stores document embeddings and enables semantic search. You can use either a **self-hosted** instance or **Qdrant Cloud**.

---

#### Option A — Self-Hosted (Docker)

```bash
docker run -d --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v ${PWD}/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

| Port | Description |
|------|-------------|
| `6333` | REST API |
| `6334` | gRPC API |

**With API key authentication (recommended):**

```bash
docker run -d --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -e QDRANT__SERVICE__API_KEY=your-api-key \
  -v ${PWD}/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

Then in `.env`:

```env
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your-api-key
```

> ⚠️ Always mount `/qdrant/storage` as a volume — without it, all data will be lost when the container restarts.

---

#### Option B — Qdrant Cloud (Managed)

No installation needed. Create a free cluster at https://cloud.qdrant.io, then grab your **Cluster URL** and **API Key** from the dashboard.

Then in `.env`:

```env
QDRANT_URL=https://your-cluster-id.aws.cloud.qdrant.io
QDRANT_API_KEY=your-qdrant-cloud-api-key
```

> 📖 For full documentation, visit: https://qdrant.tech/documentation/

---

### Verify All Services Are Running

```bash
docker ps | grep -E "minio|qdrant"
```

Both containers should show status `Up`. Also verify that the OCR Service Redis instance is running:

```bash
docker ps | grep redis
```

---

## ⚙️ Configuration

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

### `.env.example`

```env
# --- Google Configuration ---
GOOGLE_API_KEY=your_google_api_key

# --- Qdrant Configuration ---
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your_qdrant_api_key

# --- Minio Configuration ---
MINIO_ENDPOINT=host.docker.internal:9000
MINIO_ACCESS_KEY=your_minio_access_key
MINIO_SECRET_KEY=your_minio_secret_key
MINIO_USERNAME=your_minio_username
MINIO_PASSWORD=your_minio_password
CA_CERTS_PATH=/etc/minio/certs/public.crt

# --- Redis Configuration ---
REDIS_HOST=host.docker.internal
REDIS_PORT=6380
REDIS_PASSWORD=your_redis_password
```

### Variable Reference

| Variable | Description |
|----------|-------------|
| `GOOGLE_API_KEY` | Google Gemini API key for AI text processing |
| `QDRANT_URL` | Qdrant URL — `http://localhost:6333` for self-hosted or Cloud cluster URL |
| `QDRANT_API_KEY` | Qdrant API key (required if auth is enabled or using Cloud) |
| `MINIO_ENDPOINT` | MinIO server host and port |
| `MINIO_ACCESS_KEY` | MinIO access key (root user or IAM user) |
| `MINIO_SECRET_KEY` | MinIO secret key |
| `MINIO_USERNAME` | MinIO console username |
| `MINIO_PASSWORD` | MinIO console password |
| `CA_CERTS_PATH` | Path to TLS certificate for MinIO (if using HTTPS) |
| `REDIS_HOST` | Redis host — shared with OCR Service |
| `REDIS_PORT` | Redis port — shared with OCR Service |
| `REDIS_PASSWORD` | Redis auth password — shared with OCR Service |

---

## 🐳 Running with Docker

```bash
# Build and start
docker compose up --build -d

# View logs
docker logs file_parser_api -f
```

---

## 🛠️ Running Locally

```bash
# Install dependencies
uv sync

# Start the API
uv run uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

---

## 📁 Project Structure

```
file_parser/
├── src/
│   ├── core/
│   │   ├── dependencies.py     # FastAPI dependency injection (DB clients, etc.)
│   │   └── validator.py        # Request validation logic
│   ├── routes/
│   │   ├── documents.py        # Document management endpoints
│   │   └── extraction.py       # Text extraction endpoints
│   ├── schemas/
│   │   ├── exceptions.py       # Custom exception schemas
│   │   └── responses.py        # Response models
│   ├── services/
│   │   ├── docs.py             # Document storage service (MinIO)
│   │   └── extract.py          # Text extraction & embedding service (Qdrant)
│   ├── tools/
│   │   ├── utils.py            # Shared utility functions
│   │   └── word_extractor.py   # Word document text extractor
│   ├── api.py                  # FastAPI app entry point & router registration
│   └── config.py               # App settings (Pydantic)
├── documents/                  # Local document storage (if applicable)
├── docker-compose.yaml
├── Dockerfile
├── pyproject.toml
└── .env
```

---

## 📡 API Reference

### Documents

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/documents` | List all stored documents |
| `POST` | `/documents` | Upload and store a new document |
| `DELETE` | `/documents/{id}` | Delete a document by ID |

### Extraction

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/extraction` | Extract text and index embeddings from a document |
| `GET` | `/extraction/search` | Semantic search over indexed documents |

> 📌 Full interactive API docs available at `http://localhost:8000/docs` once the service is running.

---

## 📝 Notes

- MinIO is used as an S3-compatible store — you can manage buckets and objects via the Web Console at `http://localhost:9001`.
- Qdrant supports both self-hosted Docker and Qdrant Cloud — switch between them by updating `QDRANT_URL` and `QDRANT_API_KEY` in `.env`.
- `host.docker.internal` is used in `.env` to allow Docker containers to reach services running on the host machine (works on Docker Desktop and Linux with `extra_hosts: host.docker.internal:host-gateway`).
- Redis is shared with the OCR Service — make sure to use the same host, port, and password as configured in the OCR Service `.env`.