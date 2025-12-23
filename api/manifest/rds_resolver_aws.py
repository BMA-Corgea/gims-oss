import boto3
import json
from botocore.exceptions import ClientError
from functools import lru_cache

# ------------------------------------------------------------
# AWS Secrets Manager & RDS URI Construction
# ------------------------------------------------------------

# Use a cache to avoid repeatedly fetching the same secret within the app's lifecycle
@lru_cache(maxsize=16)
def _get_secret(secret_name: str, region_name: str) -> dict:
    """
    Fetches a secret from AWS Secrets Manager and returns it as a dictionary.
    """
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager', region_name=region_name)

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        print(f"ERROR: Could not retrieve secret '{secret_name}' from AWS Secrets Manager: {e}")
        raise e

    secret_string = get_secret_value_response.get('SecretString')
    if not secret_string:
        raise ValueError(f"Secret '{secret_name}' does not contain a SecretString.")

    return json.loads(secret_string)


def get_rds_connection_uri(key: str, manifest: dict, **kwargs) -> str:
    """
    Constructs a PostgreSQL database connection URI from secrets stored in AWS.

    Args:
        key: The logical database name (e.g., 'logins_db').
        manifest: The parsed rds_manifest.json content.
        **kwargs: Placeholder for any future arguments (currently unused).

    Returns:
        A full SQLAlchemy-compatible connection URI.
    """
    secret_names = manifest.get("secret_names", {})
    secret_name = secret_names.get(key)
    if not secret_name:
        raise KeyError(f"RDS key '{key}' not found in rds_manifest.json['secret_names']")

    region = manifest.get("region_name", "us-east-1")
    engine_type = manifest.get("engine", "aurora-postgresql")
    ssl_mode = manifest.get("sslmode", "require")

    # Fetch the secret JSON from AWS Secrets Manager
    secret = _get_secret(secret_name, region)

    # Extract connection details from the secret
    username = secret.get("username")
    password = secret.get("password")
    host = secret.get("host")
    port = secret.get("port")
    dbname = secret.get("dbname")

    if not all([username, password, host, port, dbname]):
        raise ValueError(f"Secret '{secret_name}' is missing required keys (username, password, host, port, dbname).")

    # Construct the appropriate URI based on engine type
    if "postgresql" in engine_type:
        # Use 'asyncpg' driver for asyncio compatibility with SQLAlchemy
        driver = "asyncpg"
        connection_uri = (
            f"postgresql+{driver}://{username}:{password}@{host}:{port}/{dbname}"
            f"?ssl={ssl_mode}"
        )
        print(f"[rds_resolver] Constructed PostgreSQL URI for '{key}'")
        return connection_uri
    else:
        raise NotImplementedError(f"Database engine '{engine_type}' is not supported yet.")
