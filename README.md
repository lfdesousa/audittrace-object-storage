# audittrace-object-storage

Object-storage provider abstraction shared by [AuditTrace-AI](https://github.com/lfdesousa/AuditTrace-AI) and `audittrace-content-control`. Exposes a single `S3ObjectStorageProvider` ABC with two backends:

- **`MinIOObjectStorageProvider`** — wraps the `minio` library (default for laptop and homelab).
- **`AWSObjectStorageProvider`** — wraps `boto3.client('s3')`, IRSA-native via boto3's default credential chain (no explicit key plumbing needed on EKS).

Anchors [ADR-006](https://github.com/lfdesousa/audittrace-deployment/blob/main/docs/adr/ADR-006-object-storage-aws-s3.md) in the deployment repo.

## Install

```bash
pip install "audittrace-object-storage @ git+https://github.com/lfdesousa/audittrace-object-storage.git@v0.1.0"
```

## Usage

```python
from audittrace_object_storage import create_provider, ObjectStorageConfig

# MinIO (laptop/homelab default)
config = ObjectStorageConfig(
    backend="minio",
    endpoint="localhost:9000",
    access_key="minioadmin",
    secret_key="...",
    secure=False,
)

# AWS S3 with IRSA (EKS production)
config = ObjectStorageConfig(
    backend="aws",
    region="eu-central-2",
    use_irsa=True,  # boto3 reads AWS_ROLE_ARN + AWS_WEB_IDENTITY_TOKEN_FILE
)

provider = create_provider(config)

with provider.get_object("memory-shared", "episodic/2026-05-21.md") as reader:
    content = reader.read().decode("utf-8")
```

## Discipline

- Per-file ≥90 % coverage gate (`make test`).
- Zero-skip policy in CI.
- All resources via `with` / contextlib — no `gc.collect()`, no `try/finally` for close.
- Module size floor: < 300 LOC per file.
- IRSA assertion: `AWSObjectStorageProvider` fails loud if `use_irsa=True` and `AWS_WEB_IDENTITY_TOKEN_FILE` env var is missing.

## License

AGPL-3.0
