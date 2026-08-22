from __future__ import annotations

import argparse
from pathlib import Path

import boto3


def read_env_value(path: Path, name: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            result = value.strip().strip('"').strip("'")
            if result:
                return result
    raise ValueError(f"{name} is missing from {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish ignored local secrets to existing AWS Secrets Manager entries.")
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--openai-secret-id", required=True)
    parser.add_argument("--admin-secret-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    client = boto3.client("secretsmanager", region_name=args.region)
    client.put_secret_value(
        SecretId=args.openai_secret_id,
        SecretString=read_env_value(args.env_file, "OPENAI_API_KEY"),
    )
    client.put_secret_value(
        SecretId=args.admin_secret_id,
        SecretString=read_env_value(args.env_file, "ADMIN_API_TOKEN"),
    )
    print("Published OPENAI_API_KEY and ADMIN_API_TOKEN to the requested secret IDs without displaying values.")
