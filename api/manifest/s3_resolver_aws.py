# api/manifest/s3_resolver_aws.py
import boto3
import os
from functools import lru_cache
from botocore.config import Config

@lru_cache(maxsize=4)
def get_s3_client(region_name: str):
    """
    Return an S3 client that prefers environment/IAM credentials
    and never loads ~/.aws/credentials.
    """
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    token = os.getenv("AWS_SESSION_TOKEN")

    # Create a session without shared config
    session = boto3.session.Session(
        region_name=region_name,
        profile_name=None  # <- prevents ~/.aws config lookup
    )

    # If you want to ensure shared config files aren’t read at all,
    # disable them through environment variables too:
    os.environ["AWS_SDK_LOAD_CONFIG"] = "0"

    # Now create the client
    if access_key and secret_key:
        client = session.client(
            "s3",
            region_name=region_name,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=token,
            config=Config(signature_version="s3v4"),
        )
    else:
        client = session.client(
            "s3",
            region_name=region_name,
            config=Config(signature_version="s3v4"),
        )

    return client
