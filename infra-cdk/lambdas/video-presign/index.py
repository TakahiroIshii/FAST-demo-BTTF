"""Video Presigned URL Lambda Handler.

Generates presigned S3 PUT URLs for uploading video files from the frontend.
The presigned URL allows the browser to upload directly to S3 without routing
the video through API Gateway or Lambda (which have size limits).
"""

import json
import os
import uuid
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

# Environment variables — fail loudly if not set
BUCKET_NAME: str = os.environ["VIDEO_BUCKET_NAME"]
CORS_ALLOWED_ORIGINS: str = os.environ["CORS_ALLOWED_ORIGINS"]

# Parse CORS origins from comma-separated string
cors_origins: list[str] = [
    origin.strip() for origin in CORS_ALLOWED_ORIGINS.split(",") if origin.strip()
]

# Presigned URL expiration in seconds (5 minutes)
PRESIGN_EXPIRATION_SECONDS: int = 300

# Maximum allowed file size (1 GB — Nova Pro S3 limit)
MAX_FILE_SIZE_BYTES: int = 1_073_741_824

# Allowed video content types that Nova Pro supports
ALLOWED_CONTENT_TYPES: set[str] = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
}

s3_client = boto3.client("s3")


def build_cors_headers(origin: str | None = None) -> Dict[str, str]:
    """
    Build CORS response headers based on the request origin.

    Args:
        origin: The Origin header from the incoming request, or None if not present.

    Returns:
        Dictionary of CORS headers to include in the response.
    """
    allowed_origin = cors_origins[0] if cors_origins else "*"
    if origin and origin in cors_origins:
        allowed_origin = origin

    return {
        "Access-Control-Allow-Origin": allowed_origin,
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "POST,OPTIONS",
        "Access-Control-Allow-Credentials": "true",
    }


def handler(event: dict, context: Any) -> Dict[str, Any]:
    """
    Lambda handler for generating presigned S3 upload URLs.

    Accepts a POST request with JSON body containing fileName and contentType.
    Returns a presigned PUT URL that the frontend can use to upload the video
    directly to S3.

    Args:
        event: API Gateway proxy event containing:
            - body (str): JSON string with:
                - fileName (str): Original filename of the video
                - contentType (str): MIME type of the video (e.g. "video/mp4")
        context: Lambda context (unused)

    Returns:
        API Gateway proxy response with:
            - uploadUrl (str): Presigned S3 PUT URL
            - s3Uri (str): The S3 URI where the file will be stored
            - objectKey (str): The S3 object key
    """
    # Extract origin for CORS
    headers = event.get("headers") or {}
    origin = headers.get("origin") or headers.get("Origin")
    cors_headers = build_cors_headers(origin=origin)

    # Handle OPTIONS preflight
    if event.get("httpMethod") == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": cors_headers,
            "body": "",
        }

    try:
        # Parse request body
        body_str = event.get("body")
        if not body_str:
            return {
                "statusCode": 400,
                "headers": cors_headers,
                "body": json.dumps({"error": "Request body is required"}),
            }

        body: dict = json.loads(body_str)
        file_name: str | None = body.get("fileName")
        content_type: str | None = body.get("contentType")

        # Validate required fields
        if not file_name or not content_type:
            return {
                "statusCode": 400,
                "headers": cors_headers,
                "body": json.dumps({"error": "fileName and contentType are required"}),
            }

        # Validate content type
        if content_type not in ALLOWED_CONTENT_TYPES:
            return {
                "statusCode": 400,
                "headers": cors_headers,
                "body": json.dumps({
                    "error": f"Unsupported content type: {content_type}. "
                    f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
                }),
            }

        # Generate a unique object key to avoid collisions
        unique_id = str(uuid.uuid4())
        # Extract file extension from the original filename
        extension = file_name.rsplit(".", 1)[-1] if "." in file_name else "mp4"
        object_key = f"uploads/{unique_id}.{extension}"

        # Generate presigned PUT URL
        upload_url: str = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": BUCKET_NAME,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=PRESIGN_EXPIRATION_SECONDS,
        )

        s3_uri = f"s3://{BUCKET_NAME}/{object_key}"

        return {
            "statusCode": 200,
            "headers": cors_headers,
            "body": json.dumps({
                "uploadUrl": upload_url,
                "s3Uri": s3_uri,
                "objectKey": object_key,
            }),
        }

    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "headers": cors_headers,
            "body": json.dumps({"error": "Invalid JSON in request body"}),
        }

    except ClientError as e:
        print(f"[ERROR] S3 presign error: {e}")
        return {
            "statusCode": 500,
            "headers": cors_headers,
            "body": json.dumps({"error": "Failed to generate upload URL"}),
        }

    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        return {
            "statusCode": 500,
            "headers": cors_headers,
            "body": json.dumps({"error": "Internal server error"}),
        }
